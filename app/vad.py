"""Energy-based voice activity detection and speech segmentation.

Splits the continuous audio stream into utterances at natural pauses so the
translator can work on complete phrases with low latency.

Key design points for low-latency, quiet-speech live captioning:
  * 20 ms frame resolution with tighter pause presets so phrase endings are
    detected quickly after the speaker pauses.
  * Sensitive, zero-mean RMS onset threshold with asymmetric noise-floor
    tracking so quiet speakers and low-volume loopback streams are picked up
    without manual gain tweaking.
  * Speech-envelope-aware continuation threshold so room reverb or background
    tail after a loud phrase is recognised as silence immediately rather than
    holding the segment open.
  * Single-frame click immunity during pauses so an isolated mouth or keyboard
    click does not reset the end-of-phrase pause timer.
  * Progressive clause-pause tightening on longer segments so fast talkers split
    at brief breath pauses before hitting the hard segment cap.
"""
from __future__ import annotations

import time
from collections import deque

import numpy as np

SPEED_PRESETS = {
    # end_frames at 20 ms/frame, max segment seconds
    "fast": {"end_frames": 11, "max_segment_s": 6.0},       # ~220 ms pause
    "balanced": {"end_frames": 16, "max_segment_s": 9.0},   # ~320 ms pause
    "accurate": {"end_frames": 26, "max_segment_s": 14.0},  # ~520 ms pause
}


class Segmenter:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        start_frames: int = 2,
        end_frames: int = 16,
        max_segment_s: float = 9.0,
        min_speech_s: float = 0.18,
        preroll_ms: int = 260,
        trailing_pad_ms: int = 100,
        energy_threshold: float = 0.0018,
        noise_multiplier: float = 2.8,
        grace_after_pause_s: float = 0.35,
    ) -> None:
        self.sr = sample_rate
        self.frame_ms = frame_ms
        self.frame_len = int(sample_rate * frame_ms / 1000)
        self.start_frames = start_frames
        self.end_frames = end_frames
        self.max_frames = int(max_segment_s * 1000 / frame_ms)
        self.min_voiced_frames = max(1, int(min_speech_s * 1000 / frame_ms))
        self.energy_threshold = energy_threshold
        self.noise_multiplier = noise_multiplier
        self.grace = grace_after_pause_s

        self._trailing_keep_frames = max(2, int(trailing_pad_ms / frame_ms))
        self._overlap_frames = max(2, int(80 / frame_ms))
        self._preroll = deque(maxlen=max(1, int(preroll_ms / frame_ms)))
        self._buf = np.zeros(0, dtype=np.float32)
        self._state = "idle"
        self._segment: list[np.ndarray] = []
        self._run = 0
        self._voiced = 0
        self._silence = 0
        self._resume_run = 0
        self._speech_rms = 0.0
        self._noise = 0.0006
        self._paused = False
        self._paused_until = 0.0

    @classmethod
    def from_speed(cls, speed: str = "balanced", **kwargs) -> "Segmenter":
        preset = SPEED_PRESETS.get(speed, SPEED_PRESETS["balanced"])
        params = {
            "end_frames": preset["end_frames"],
            "max_segment_s": preset["max_segment_s"],
        }
        params.update(kwargs)
        return cls(**params)

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
        self._resume_run = 0
        self._speech_rms = 0.0
        self._preroll.clear()
        self._buf = np.zeros(0, dtype=np.float32)

    def push(self, audio: np.ndarray) -> list[np.ndarray]:
        """Feed 16 kHz mono float32 audio; return any completed utterances."""
        if self._paused or time.monotonic() < self._paused_until:
            self._buf = np.zeros(0, dtype=np.float32)
            self._preroll.clear()
            return []

        if len(audio) == 0:
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

    def _effective_end_frames(self, seg_frames: int) -> int:
        """Tighten pause requirement gradually on long continuous segments."""
        half = self.max_frames // 2
        if seg_frames <= half or self.max_frames <= half:
            return self.end_frames
        min_end = max(6, self.end_frames // 2)
        if min_end >= self.end_frames:
            return self.end_frames
        progress = min(1.0, (seg_frames - half) / float(self.max_frames - half))
        return max(min_end, int(round(
            self.end_frames - progress * (self.end_frames - min_end)
        )))

    def _handle(self, frame: np.ndarray) -> np.ndarray | None:
        # Remove per-frame DC offset so mic bias never masks quiet pauses.
        centered = frame - float(np.mean(frame))
        rms = float(np.sqrt(np.mean(centered ** 2)) + 1e-12)
        onset_threshold = max(self.energy_threshold,
                              self._noise * self.noise_multiplier)

        if self._state == "idle":
            voiced = rms > onset_threshold
            if rms < self._noise:
                # Settle quickly down to a quiet digital loopback floor.
                self._noise = 0.92 * self._noise + 0.08 * rms
            elif not voiced:
                # Track steady background hum slowly without speech pollution.
                self._noise = 0.995 * self._noise + 0.005 * rms

            self._preroll.append(frame)
            self._run = self._run + 1 if voiced else 0
            if self._run >= self.start_frames:
                self._state = "speech"
                self._segment = list(self._preroll)
                self._voiced = self._run
                self._silence = 0
                self._resume_run = 0
                self._speech_rms = rms
            return None

        # state == speech
        # Hysteresis below onset keeps quiet speech steady; envelope floor lets
        # louder phrases end promptly even when room reverb lingers above onset.
        end_threshold = max(
            onset_threshold * 0.85,
            min(self._speech_rms * 0.10, 0.008),
        )
        voiced = rms > end_threshold
        self._segment.append(frame)

        if voiced:
            self._speech_rms = 0.85 * self._speech_rms + 0.15 * rms
            if self._silence == 0:
                self._voiced += 1
            else:
                # Require 2 consecutive voiced frames to cancel an active pause,
                # so an isolated 20 ms click does not reset end-of-phrase timing.
                self._resume_run += 1
                if self._resume_run >= 2:
                    self._voiced += self._resume_run
                    self._silence = 0
                    self._resume_run = 0
        else:
            self._resume_run = 0
            self._silence += 1

        n = len(self._segment)
        eff_end = self._effective_end_frames(n)
        hit_silence = self._silence >= eff_end
        hit_max = n >= self.max_frames

        if hit_silence or hit_max:
            enough = self._voiced >= self.min_voiced_frames
            if hit_silence and self._silence > self._trailing_keep_frames:
                trim = self._silence - self._trailing_keep_frames
                emit_frames = (self._segment[:-trim]
                               if trim < len(self._segment) else self._segment)
            else:
                emit_frames = self._segment
            seg = np.concatenate(emit_frames)

            if hit_max and not hit_silence:
                # Cut long monologues seamlessly, keeping a brief boundary
                # overlap so split syllables are not lost.
                overlap = self._segment[-self._overlap_frames:]
                self._segment = list(overlap)
                self._voiced = len(overlap)
                self._silence = 0
                self._resume_run = 0
            else:
                self._state = "idle"
                self._segment = []
                self._run = 0
                self._voiced = 0
                self._silence = 0
                self._resume_run = 0
                self._speech_rms = 0.0
                self._preroll.clear()
            return seg if enough else None
        return None
