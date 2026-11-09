"""Wires capture -> resample -> VAD segmentation -> translation -> output.

Threading layout (important for keeping latency bounded):

    audio callback  -> audio_q -> segment thread -> utterance_q -> translate thread

Translation runs on its OWN thread. In an earlier version it ran inside the
audio-consuming loop, so a model slower than realtime (e.g. `small` on a modest
CPU measured ~3x realtime) caused the raw audio queue to overflow and drop
chunks *mid-sentence*, garbling the speech. Now the segment thread always keeps
up, and if the translator falls behind we drop the OLDEST WHOLE utterance and
tell the user, so every subtitle is still a clean sentence.
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .audio import AudioCapture, Device, Resampler, TARGET_RATE
from .translator import Translator
from .vad import Segmenter

MAX_BACKLOG = 2  # whole utterances waiting to be translated


@dataclass
class Event:
    kind: str          # translation|status|error|level|stopped|perf
    text: str = ""
    original: str = ""
    language: str = ""
    probability: float = 0.0
    level: float = 0.0
    rtf: float = 0.0
    dropped: int = 0


class Pipeline:
    def __init__(self, translator: Translator, speaker, events: "queue.Queue[Event]",
                 want_original: bool = False, skip_english: bool = False,
                 speed: str = "balanced") -> None:
        self.translator = translator
        self.speaker = speaker
        self.events = events
        self.want_original = want_original
        self.skip_english = skip_english
        self.speed = speed

        self._audio_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=512)
        self._utt_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=MAX_BACKLOG)
        self._capture: AudioCapture | None = None
        self._seg_thread: threading.Thread | None = None
        self._work_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._speaking = threading.Event()
        # Set only once the model is loaded. Until then we must not segment or
        # queue speech: the model can take seconds to load (minutes on a first
        # download), and queueing during that window overflowed the backlog and
        # produced a bogus "Skipped phrases - use a smaller model" warning.
        self._ready = threading.Event()
        self._segmenter: Segmenter | None = None
        self._rtf_history: deque = deque(maxlen=5)
        self._dropped = 0
        self._warned = False
        self._inferences = 0
        self._last_drop_notice = 0.0
        self.running = False

    def _emit(self, ev: Event) -> None:
        try:
            self.events.put_nowait(ev)
        except queue.Full:
            pass

    def _on_speaking(self, speaking: bool) -> None:
        if speaking:
            self._speaking.set()
        else:
            self._speaking.clear()
        if self._segmenter is not None:
            self._segmenter.set_paused(speaking)

    def start(self, device: Device) -> None:
        if self.running:
            return
        self._stop.clear()
        self._ready.clear()
        self._dropped = 0
        self._warned = False
        self._inferences = 0
        self._last_drop_notice = 0.0
        self._rtf_history.clear()
        self._segmenter = Segmenter.from_speed(self.speed)
        self.translator.reset_session()

        if self.speaker is not None:
            self.speaker.on_speaking = self._on_speaking

        self._capture = AudioCapture(
            device, self._audio_q,
            on_error=lambda m: self._emit(Event("error", text=m)),
        )
        self._capture.start()
        self.running = True

        self._seg_thread = threading.Thread(target=self._segment_loop, daemon=True)
        self._work_thread = threading.Thread(target=self._translate_loop, daemon=True)
        self._seg_thread.start()
        self._work_thread.start()
        self._emit(Event("status", text=f"Listening to: {device.name}"))

    # ---- stage 1: resample + segment (always keeps up) ----
    def _segment_loop(self) -> None:
        assert self._capture is not None
        resampler = Resampler(self._capture.device.rate, TARGET_RATE)
        last_level_emit = 0.0
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue

                mono16k = resampler(chunk)
                level = float(np.sqrt(np.mean(mono16k ** 2)) + 1e-9)
                now = time.monotonic()
                if now - last_level_emit > 0.1:
                    self._emit(Event("level", level=level))
                    last_level_emit = now

                if self._segmenter is None:
                    continue

                # Discard audio until the model is ready. The level meter above
                # still runs, so the user can confirm the right device while the
                # model loads, but nothing is queued and nothing is "skipped".
                if not self._ready.is_set():
                    continue

                for utt in self._segmenter.push(mono16k):
                    self._enqueue(utt)

            if self._segmenter is not None and self._ready.is_set():
                tail = self._segmenter.flush()
                if tail is not None:
                    self._enqueue(tail)
        except Exception as exc:
            self._emit(Event("error", text=str(exc)))

    def _enqueue(self, utt: np.ndarray) -> None:
        """Keep latency bounded: drop the oldest whole utterance if behind.

        The ready gate is enforced here, at the single queue boundary, so no
        caller can queue speech before the model has loaded.
        """
        if not self._ready.is_set():
            return
        while True:
            try:
                self._utt_q.put_nowait(utt)
                return
            except queue.Full:
                try:
                    self._utt_q.get_nowait()
                    self._dropped += 1
                    # Rate-limit the notice: one line per 20 s, not per phrase.
                    now = time.monotonic()
                    if now - self._last_drop_notice > 20.0:
                        self._last_drop_notice = now
                        self._emit(Event("perf", dropped=self._dropped))
                except queue.Empty:
                    return

    # ---- stage 2: translate (may be slower than realtime) ----
    def _translate_loop(self) -> None:
        try:
            self.translator.load()
        except Exception as exc:
            self._emit(Event("error", text=str(exc)))
            self.running = False
            self._emit(Event("stopped"))
            return

        # Model is live: start accepting speech from here, with a clean
        # segmenter so no stale pre-load audio leaks into the first phrase.
        if self._segmenter is not None:
            self._segmenter = Segmenter.from_speed(self.speed)
        self._ready.set()
        self._emit(Event("status", text="Ready — listening for speech."))

        while not self._stop.is_set() or not self._utt_q.empty():
            try:
                utt = self._utt_q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._translate(utt)

        self.running = False
        self._emit(Event("stopped"))

    def _slow_advice(self, rtf: float) -> str:
        """Give advice that fits the user's ACTUAL model and device.

        The old fixed message told everyone to "use a smaller model" and "set
        Processing to cuda" — useless when already on tiny + cuda, which is a
        real configuration users hit.
        """
        model = getattr(self.translator, "model_size", "?")
        device = getattr(self.translator, "actual_device", "cpu")
        sizes = ["tiny", "base", "small", "medium", "large-v3"]
        smaller = None
        if model in sizes:
            i = sizes.index(model)
            if i > 0:
                smaller = sizes[i - 1]

        parts = [f"Translation is running ~{rtf:.1f}x slower than live speech "
                 f"({model} on {device.upper()})."]
        if smaller:
            parts.append(f"Try the '{smaller}' model in Settings.")
        if device != "cuda":
            parts.append("If you have an NVIDIA GPU, set Processing to cuda.")
        if self.want_original:
            parts.append("Turn off 'Also show the original language text' — it "
                         "runs the model twice per phrase.")
        if not smaller and device == "cuda" and not self.want_original:
            # Already the fastest configuration: the bottleneck is elsewhere.
            parts.append("This is already the fastest setup, so the GPU may be "
                         "busy or falling back to CPU. Close other GPU-heavy "
                         "apps, and try Response speed = fast so phrases are "
                         "shorter.")
        parts.append("Occasional skips are normal during long sentences.")
        return " ".join(parts)

    def _translate(self, audio: np.ndarray) -> None:
        if self._speaking.is_set():
            return
        duration = len(audio) / TARGET_RATE
        t0 = time.monotonic()
        try:
            result = self.translator.process(
                audio,
                want_original=self.want_original,
                skip_english=self.skip_english,
            )
        except Exception as exc:
            self._emit(Event("error", text=f"Translation failed: {exc}"))
            return
        elapsed = time.monotonic() - t0
        self._inferences += 1

        # The first inference after load is always slow (CUDA kernel warm-up,
        # memory allocation). Never judge performance from it.
        if duration > 0.5 and self._inferences > 1:
            self._rtf_history.append(elapsed / duration)
            avg = sum(self._rtf_history) / len(self._rtf_history)
            if avg > 0.9 and not self._warned and len(self._rtf_history) >= 3:
                self._warned = True
                self._emit(Event("perf", rtf=avg, text=self._slow_advice(avg)))

        if result is None:
            return
        self._emit(Event(
            "translation", text=result.english, original=result.original,
            language=result.language, probability=result.probability,
        ))
        if self.speaker is not None and self.speaker.enabled:
            self.speaker.say(result.english)

    def stop(self) -> None:
        self._stop.set()
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        for t in (self._seg_thread, self._work_thread):
            if t is not None:
                t.join(timeout=5.0)
        self._seg_thread = None
        self._work_thread = None
        self.running = False
