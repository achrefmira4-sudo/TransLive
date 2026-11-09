"""Speech recognition + translation to English using faster-whisper.

Whisper has a built-in `translate` task that goes straight from any supported
language to English, so no separate translation service or API key is needed.
Everything runs locally on your PC.

Problems handled here for live use:
  1. Quiet speakers and low-volume loopback streams get lost or drowned out by
     isolated clicks.
     Fix: DC-offset removal + active-speech RMS normalization (up to +35 dB)
     with a smooth soft-knee limiter, paired with a more sensitive Silero VAD
     threshold.
  2. Greedy single-temperature decoding makes avoidable word-order and
     vocabulary errors and can get stuck in repetition loops.
     Fix: beam search (beam=3 on CPU, 5 on CUDA), temperature fallback
     (0.0, 0.2, 0.4), and bounded short-lived context carry-over across
     consecutive phrases.
  3. Short clips and music intros get mis-detected (Arabic -> "Japanese") or
     hallucinated.
     Fix: optional language lock, session language prior for short clips, and
     calibrated segment-level gating.
  4. Transcribing the original text doubles the compute cost.
     Fix: it is optional and off by default.
"""
from __future__ import annotations

import re
import threading
import time
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
CONTEXT_TTL_S = 15.0
CONTEXT_MAX_CHARS = 160

# Tuned Silero VAD options: lower speech threshold picks up quiet/murmured
# voices and soft VoIP streams while still rejecting noise, tones, and music.
VAD_PARAMETERS = {
    "threshold": 0.35,
    "neg_threshold": 0.20,
    "min_speech_duration_ms": 120,
    "min_silence_duration_ms": 250,
    "speech_pad_ms": 250,
}

FALLBACK_TEMPERATURES = (0.0, 0.2, 0.4)


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


def normalize_audio(
    audio: np.ndarray,
    target_rms: float = 0.14,
    max_gain: float = 60.0,
) -> np.ndarray | None:
    """Boost quiet speech to a consistent level without transient-click bias.

    Plain peak normalization fails in two common cases:
      * Very quiet speakers (peak < 0.05) were previously capped at 19x gain,
        arriving at Whisper far below optimal level.
      * A single mouse/keyboard click with peak ~0.4 prevented boosting a quiet
        voice sitting at RMS 0.003 in the same segment.

    Measuring RMS over active 20 ms frames ignores isolated transients when
    choosing gain, and a smooth tanh knee above 0.85 keeps any transient click
    from clipping harshly.
    """
    if len(audio) < 1600:  # < 100 ms
        return None

    x = audio.astype(np.float32, copy=False)
    x = x - float(np.mean(x))

    peak = float(np.max(np.abs(x)))
    if peak < 1e-3:
        return None

    frame_len = 320  # 20 ms @ 16 kHz
    n_frames = len(x) // frame_len
    if n_frames > 0:
        frames = x[: n_frames * frame_len].reshape(n_frames, frame_len)
        frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
    else:
        frame_rms = np.array([float(np.sqrt(np.mean(x ** 2)))], dtype=np.float32)

    max_rms = float(np.max(frame_rms))
    if max_rms < 5e-4:
        return None

    ref_rms = float(np.percentile(frame_rms, 95))
    if ref_rms < 5e-4:
        ref_rms = max_rms

    active = frame_rms[frame_rms >= max(5e-4, 0.30 * ref_rms)]
    active_rms = (
        float(np.percentile(active, 75)) if len(active) > 0 else ref_rms
    )

    if active_rms < target_rms:
        gain = min(max_gain, target_rms / max(active_rms, 1e-6))
    elif peak > 0.95:
        gain = 0.95 / peak
    else:
        gain = 1.0

    out = (x * gain).astype(np.float32, copy=False)

    # Smooth C1 soft-knee limiter above 0.85 so isolated clicks never hard-clip.
    knee = 0.85
    abs_out = np.abs(out)
    above = abs_out > knee
    if np.any(above):
        out = out.copy()
        excess = (abs_out[above] - knee) / (1.0 - knee)
        out[above] = np.sign(out[above]) * (
            knee + (1.0 - knee) * np.tanh(excess)
        )

    return np.clip(out, -1.0, 1.0).astype(np.float32, copy=False)


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
    dialectal Arabic legitimately scores around -2.0. Keep the floor very low
    and only reject when the model is *also* confident there is no speech.
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


def _is_prompt_echo(text: str, prompt: str | None) -> bool:
    if not prompt or not text:
        return False
    nt = _normalize(text)
    np_ = _normalize(prompt)
    return bool(nt and np_ and len(nt) >= 6 and nt in np_)


class Translator:
    """Loads a Whisper model once and reuses it for every utterance."""

    def __init__(self, model_size: str = "base", device: str = "auto",
                 compute_type: str = "auto", download_root: str | None = None,
                 on_status=None, language: str | None = None,
                 cpu_threads: int = 0, beam_size: int | None = None) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self.on_status = on_status
        self.language = language  # None = auto-detect
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        self.model = None
        self.actual_device = "cpu"
        self._lock = threading.Lock()
        self._recent: deque = deque(maxlen=10)
        self._context: deque[str] = deque(maxlen=2)
        self._context_ts: float = 0.0

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def reset_session(self) -> None:
        self._recent.clear()
        self._context.clear()
        self._context_ts = 0.0

    def _effective_beam_size(self) -> int:
        if self.beam_size is not None:
            return max(1, int(self.beam_size))
        return 5 if self.actual_device == "cuda" else 3

    def _active_prompt(self) -> str | None:
        if not self._context:
            return None
        if time.monotonic() - self._context_ts > CONTEXT_TTL_S:
            self._context.clear()
            return None
        joined = " ".join(self._context).strip()
        if len(joined) > CONTEXT_MAX_CHARS:
            joined = joined[-CONTEXT_MAX_CHARS:].lstrip()
        return joined or None

    def _remember_context(self, english: str, quality: float) -> None:
        if quality > -0.95 and len(english.split()) >= 3 and not is_hallucination(english):
            self._context.append(english.strip())
            self._context_ts = time.monotonic()

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
        counts = Counter(lang for lang, prob in self._recent if prob >= 0.60)
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
    def _run_translate(self, audio: np.ndarray, language: str | None,
                       initial_prompt: str | None = None):
        beam = self._effective_beam_size()
        segments, info = self.model.transcribe(
            audio,
            task="translate",
            language=language,
            beam_size=beam,
            best_of=3,
            temperature=FALLBACK_TEMPERATURES,
            condition_on_previous_text=False,
            prompt_reset_on_temperature=0.3,
            initial_prompt=initial_prompt,
            vad_filter=True,
            vad_parameters=dict(VAD_PARAMETERS),
            without_timestamps=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.35,
            compression_ratio_threshold=2.4,
        )
        # Check VAD speech duration before consuming the lazy segment generator
        # so pure noise or tiny clicks skip Whisper decoding altogether.
        if getattr(info, "duration_after_vad", 1.0) < 0.15:
            return "", info, -99.0

        kept = [s for s in segments if _keep_segment(s)]
        text = " ".join(s.text.strip() for s in kept).strip()
        return text, info, _quality(kept)

    def process(self, audio: np.ndarray, want_original: bool = False,
                skip_english: bool = False) -> TranslationResult | None:
        if self.model is None:
            self.load()

        prepared = normalize_audio(audio)
        if prepared is None:
            return None

        forced = self.language
        with self._lock:
            prompt = self._active_prompt()
            english, info, quality = self._run_translate(
                prepared, forced, initial_prompt=prompt,
            )

            if getattr(info, "duration_after_vad", 1.0) < 0.15:
                return None

            # Guard against low-confidence prompt echoing on faint/noisy clips.
            if prompt and _is_prompt_echo(english, prompt) and quality < -0.85:
                english, info, quality = self._run_translate(
                    prepared, forced, initial_prompt=None,
                )

            language = info.language or "?"
            probability = float(getattr(info, "language_probability", 0.0))
            retried = False

            # Fix short-clip language flips using what the call has been in.
            bad_initial = (not english) or is_hallucination(english)
            if (forced is None
                    and (probability < 0.70 or bad_initial)
                    and len(prepared) / SAMPLE_RATE < 4.5):
                prior = self._dominant_language()
                if prior and prior != language:
                    alt_text, alt_info, alt_quality = self._run_translate(
                        prepared, prior, initial_prompt=prompt,
                    )
                    if (alt_text and not is_hallucination(alt_text)
                            and ((bad_initial and alt_quality > -1.8)
                                 or alt_quality > quality + 0.05)):
                        english = alt_text
                        language = alt_info.language
                        probability = float(
                            getattr(alt_info, "language_probability", 0.0))
                        quality = alt_quality
                        retried = True

            if not english or is_hallucination(english):
                return None

            self._remember(language, probability if not forced else 1.0)
            self._remember_context(english, quality)

            if skip_english and language == "en":
                return None

            original = ""
            if want_original and language != "en":
                beam = self._effective_beam_size()
                segs, _ = self.model.transcribe(
                    prepared,
                    task="transcribe",
                    language=language,
                    beam_size=beam,
                    best_of=3,
                    temperature=FALLBACK_TEMPERATURES,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters=dict(VAD_PARAMETERS),
                    without_timestamps=True,
                    no_speech_threshold=0.6,
                    log_prob_threshold=-1.35,
                    compression_ratio_threshold=2.4,
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
