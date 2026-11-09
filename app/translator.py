"""Speech recognition + translation to English using faster-whisper.

Whisper has a built-in `translate` task that goes straight from any supported
language to English, so no separate translation service or API key is needed.
Everything runs locally on your PC.

Three problems showed up in testing on real Arabic/French/German recordings and
are handled here:
  1. Short clips and music intros get mis-detected (Arabic -> "Japanese").
     Fix: optional language lock, plus a session language prior used to retry
     low-confidence short utterances.
  2. Music and background noise produce invented sentences.
     Fix: segment-level no_speech / logprob / compression gating.
  3. Transcribing the original text doubles the compute cost.
     Fix: it is optional and off by default.
"""
from __future__ import annotations

import re
import threading
from collections import Counter, deque

import numpy as np

try:
    from faster_whisper import WhisperModel

    HAS_WHISPER = True
except Exception:  # pragma: no cover
    WhisperModel = None
    HAS_WHISPER = False


MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]

MODEL_NOTES = {
    "tiny": "Fastest, rough accuracy (~75 MB)",
    "base": "Best starting point on CPU (~145 MB)",
    "small": "More accurate; needs a fast CPU or GPU (~485 MB)",
    "medium": "High accuracy, GPU recommended (~1.5 GB)",
    "large-v3": "Best accuracy, GPU required for live use (~3 GB)",
}

# Whisper emits these on silence, music or noise.
HALLUCINATIONS = {
    "thank you", "thanks", "thank you very much", "thanks for watching",
    "thank you for watching", "please subscribe", "subscribe",
    "you", "bye", "bye bye", "goodbye", "the end", "amen",
    "subtitles by the amara.org community", "subtitle by",
    "please like and subscribe", "see you next time", "peace",
    "i'm sorry", "sorry", "oh", "uh", "um", "hmm", "mm", "yeah",
    "music", "applause", "laughter", "silence", "foreign",
    "www.mooji.org", "god bless you",
}

_PUNCT = re.compile(r"[\s\.\,\!\?\-–—\"'“”‘’…:;()\[\]]+")

SAMPLE_RATE = 16000


def _normalize(text: str) -> str:
    return _PUNCT.sub(" ", text.lower()).strip()


def is_hallucination(text: str) -> bool:
    n = _normalize(text)
    if not n:
        return True
    if n in HALLUCINATIONS:
        return True
    if len(n) <= 2:
        return True
    words = n.split()
    if len(words) >= 4 and len(set(words)) == 1:
        return True
    # "thank you thank you thank you" style loops
    if len(words) >= 6:
        bigrams = list(zip(words, words[1:]))
        if len(set(bigrams)) <= len(bigrams) / 3:
            return True
    return False


class TranslationResult:
    def __init__(self, english: str, original: str, language: str,
                 probability: float, retried: bool = False) -> None:
        self.english = english
        self.original = original
        self.language = language
        self.probability = probability
        self.retried = retried


def _keep_segment(seg) -> bool:
    """Reject only clearly-bad segments.

    Thresholds are calibrated from tests/calibrate_gating.py on real recordings:

        real Arabic speech : no_speech 0.02-0.22, logprob -0.68 .. -2.27
        real French/German : no_speech 0.01-0.02, logprob -0.68 .. -0.78
        silence / white noise / music / pure tone : VAD returns NO segments

    Whisper's own VAD already removes noise and music before we get here, so an
    aggressive logprob cutoff is both unnecessary and harmful: translating
    dialectal Arabic legitimately scores around -2.0. An earlier `-1.2` cutoff
    silently discarded every real Arabic utterance. Keep the floor very low and
    only reject when the model is *also* confident there is no speech.
    """
    no_speech = getattr(seg, "no_speech_prob", 0.0)
    logprob = getattr(seg, "avg_logprob", 0.0)
    compression = getattr(seg, "compression_ratio", 1.0)

    # Confident it is not speech AND a poor decode -> drop.
    if no_speech > 0.80 and logprob < -1.0:
        return False
    # Pathological decode only; real dialectal speech reaches ~-2.3.
    if logprob < -3.0:
        return False
    # Degenerate repetition loops.
    if compression > 2.8:
        return False
    return True


def _quality(segments) -> float:
    if not segments:
        return -99.0
    return sum(getattr(s, "avg_logprob", -99.0) for s in segments) / len(segments)


class Translator:
    """Loads a Whisper model once and reuses it for every utterance."""

    def __init__(self, model_size: str = "base", device: str = "auto",
                 compute_type: str = "auto", download_root: str | None = None,
                 on_status=None, language: str | None = None,
                 cpu_threads: int = 0) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self.on_status = on_status
        self.language = language  # None = auto-detect
        self.cpu_threads = cpu_threads
        self.model = None
        self.actual_device = "cpu"
        self._lock = threading.Lock()
        self._recent: deque = deque(maxlen=10)

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def reset_session(self) -> None:
        self._recent.clear()

    def load(self) -> None:
        if not HAS_WHISPER:
            raise RuntimeError(
                "faster-whisper is not installed.\n"
                "Run: pip install -r requirements.txt"
            )
        with self._lock:
            if self.model is not None:
                return

            device = self.device
            compute = self.compute_type
            if device == "auto":
                device = "cuda" if self._cuda_available() else "cpu"
            if compute == "auto":
                compute = "float16" if device == "cuda" else "int8"

            kwargs = {}
            if self.cpu_threads and device == "cpu":
                kwargs["cpu_threads"] = self.cpu_threads

            self._status(f"Loading {self.model_size} model on {device} "
                         f"({compute})… first run downloads the model.")
            try:
                self.model = WhisperModel(
                    self.model_size, device=device, compute_type=compute,
                    download_root=self.download_root, **kwargs,
                )
                self.actual_device = device
            except Exception as exc:
                if device == "cuda":
                    self._status(f"GPU unavailable ({exc}). Falling back to CPU.")
                    self.model = WhisperModel(
                        self.model_size, device="cpu", compute_type="int8",
                        download_root=self.download_root, **kwargs,
                    )
                    self.actual_device = "cpu"
                else:
                    raise
            self._status(f"Model ready on {self.actual_device.upper()}.")

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import ctranslate2

            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    def unload(self) -> None:
        with self._lock:
            self.model = None

    # ---- language prior -------------------------------------------------
    def _dominant_language(self) -> str | None:
        """The language this conversation has mostly been in, if clear."""
        if len(self._recent) < 2:
            return None
        counts = Counter(lang for lang, prob in self._recent if prob >= 0.65)
        if not counts:
            return None
        lang, hits = counts.most_common(1)[0]
        if hits >= 2 and hits >= len(self._recent) * 0.5:
            return lang
        return None

    def _remember(self, language: str, probability: float) -> None:
        if language:
            self._recent.append((language, probability))

    # ---- inference ------------------------------------------------------
    def _run_translate(self, audio: np.ndarray, language: str | None):
        segments, info = self.model.transcribe(
            audio,
            task="translate",
            language=language,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300,
                            "speech_pad_ms": 200},
            without_timestamps=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        kept = [s for s in segments if _keep_segment(s)]
        text = " ".join(s.text.strip() for s in kept).strip()
        return text, info, _quality(kept)

    def process(self, audio: np.ndarray, want_original: bool = False,
                skip_english: bool = False) -> TranslationResult | None:
        if self.model is None:
            self.load()

        if len(audio) < 1600:  # < 100 ms
            return None

        # Peak-normalize quietly captured audio so Whisper hears it clearly.
        peak = float(np.max(np.abs(audio)))
        if peak < 1e-4:
            return None
        audio = audio / max(peak, 0.05) * 0.95
        audio = np.clip(audio, -1.0, 1.0).astype(np.float32)

        forced = self.language
        with self._lock:
            english, info, quality = self._run_translate(audio, forced)

            # VAD inside Whisper found almost no speech -> music/noise.
            if getattr(info, "duration_after_vad", 1.0) < 0.25:
                return None

            language = info.language or "?"
            probability = float(getattr(info, "language_probability", 0.0))
            retried = False

            # Fix short-clip language flips using what the call has been in.
            if (forced is None and probability < 0.60
                    and len(audio) / SAMPLE_RATE < 4.0):
                prior = self._dominant_language()
                if prior and prior != language:
                    alt_text, alt_info, alt_quality = self._run_translate(audio, prior)
                    if alt_text and alt_quality > quality + 0.15:
                        english = alt_text
                        language = alt_info.language
                        probability = float(
                            getattr(alt_info, "language_probability", 0.0))
                        retried = True

        if not english or is_hallucination(english):
            return None

        self._remember(language, probability if not forced else 1.0)

        if skip_english and language == "en":
            return None

        original = ""
        if want_original and language != "en":
            with self._lock:
                segs, _ = self.model.transcribe(
                    audio, task="transcribe", language=language,
                    beam_size=1, best_of=1, temperature=0.0,
                    condition_on_previous_text=False, vad_filter=False,
                    without_timestamps=True,
                )
                original = " ".join(
                    s.text.strip() for s in segs if _keep_segment(s)
                ).strip()
            if is_hallucination(original):
                original = ""

        return TranslationResult(
            english=english, original=original, language=language,
            probability=probability if not forced else 1.0, retried=retried,
        )
