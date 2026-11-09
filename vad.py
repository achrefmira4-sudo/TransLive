"""Energy-based voice activity detection and speech segmentation.

Splits the continuous audio stream into utterances at natural pauses so the
translator can work on complete phrases with low latency.
"""
from __future__ import annotations

import time
from collections import deque

import numpy as np

SPEED_PRESETS = {
    # end_silence_frames at 30 ms/frame, max segment seconds
    "fast": {"end_frames": 12, "max_segment_s": 8.0},      # ~360 ms pause
    "balanced": {"end_frames": 20, "max_segment_s": 12.0},  # ~600 ms pause
    "accurate": {"end_frames": 32, "max_segment_s": 18.0},  # ~960 ms pause
}


class Segmenter:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        start_frames: int = 3,
        end_frames: int = 20,
        max_segment_s: float = 12.0,
        min_speech_s: float = 0.25,
        preroll_ms: int = 300,
        energy_threshold: float = 0.006,
        grace_after_pause_s: float = 0.35,
    ) -> None:
        self.sr = sample_rate
        self.frame_ms = frame_ms
        self.frame_len = int(sample_rate * frame_ms / 1000)
        self.start_frames = start_frames
        self.end_frames = end_frames
        self.max_frames = int(max_segment_s * 1000 / frame_ms)
        self.min_voiced_frames = int(min_speech_s * 1000 / frame_ms)
        self.energy_threshold = energy_threshold
        self.grace = grace_after_pause_s

        self._preroll = deque(maxlen=max(1, int(preroll_ms / frame_ms)))
        self._buf = np.zeros(0, dtype=np.float32)
        self._state = "idle"
        self._segment: list[np.ndarray] = []
        self._run = 0
        self._voiced = 0
        self._silence = 0
        self._noise = 0.002
        self._paused = False
        self._paused_until = 0.0

    @classmethod
    def from_speed(cls, speed: str = "balanced", **kwargs) -> "Segmenter":
        preset = SPEED_PRESETS.get(speed, SPEED_PRESETS["balanced"])
        return cls(end_frames=preset["end_frames"],
                   max_segment_s=preset["max_segment_s"], **kwargs)

    def set_paused(self, paused: bool) -> None:
        """Pause while our own TTS is speaking to avoid a feedback loop."""
        if paused and not self._paused:
            self._reset()
        if not paused and self._paused:
            self._paused_until = time.monotonic() + self.grace
        self._paused = paused

    def _reset(self) -> None:
        self._state = "idle"
        self._segment = []
        self._run = 0
        self._voiced = 0
        self._silence = 0
        self._preroll.clear()
        self._buf = np.zeros(0, dtype=np.float32)

    def push(self, audio: np.ndarray) -> list[np.ndarray]:
        """Feed 16 kHz mono float32 audio; return any completed utterances."""
        if self._paused or time.monotonic() < self._paused_until:
            self._buf = np.zeros(0, dtype=np.float32)
            self._preroll.clear()
            return []

        self._buf = np.concatenate([self._buf, audio])
        out: list[np.ndarray] = []
        while len(self._buf) >= self.frame_len:
            frame = self._buf[: self.frame_len]
            self._buf = self._buf[self.frame_len :]
            seg = self._handle(frame)
            if seg is not None:
                out.append(seg)
        return out

    def flush(self) -> np.ndarray | None:
        """Force-end the current utterance (e.g. when stopping)."""
        if self._state == "speech" and self._voiced >= self.min_voiced_frames:
            seg = np.concatenate(self._segment)
            self._reset()
            return seg
        self._reset()
        return None

    def _handle(self, frame: np.ndarray) -> np.ndarray | None:
        rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-12)
        threshold = max(self.energy_threshold, self._noise * 3.5)
        voiced = rms > threshold

        if self._state == "idle":
            # Adapt the noise floor only while nobody is talking.
            self._noise = 0.995 * self._noise + 0.005 * rms
            self._preroll.append(frame)
            self._run = self._run + 1 if voiced else 0
            if self._run >= self.start_frames:
                self._state = "speech"
                self._segment = list(self._preroll)
                self._voiced = self._run
                self._silence = 0
            return None

        # state == speech
        self._segment.append(frame)
        if voiced:
            self._voiced += 1
            self._silence = 0
        else:
            self._silence += 1

        n = len(self._segment)
        hit_silence = self._silence >= self.end_frames
        hit_max = n >= self.max_frames

        if hit_silence or hit_max:
            seg = np.concatenate(self._segment)
            enough = self._voiced >= self.min_voiced_frames
            if hit_max and not hit_silence:
                # Cut long monologues but keep listening seamlessly.
                self._segment = []
                self._voiced = 0
                self._silence = 0
            else:
                self._state = "idle"
                self._segment = []
                self._run = 0
                self._voiced = 0
                self._silence = 0
                self._preroll.clear()
            return seg if enough else None
        return None
