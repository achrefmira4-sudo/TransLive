"""Calibrate noise gating against real speech vs silence/music/noise.

Prints the segment stats Whisper reports so thresholds are chosen from data,
not guesses. Real speech must survive; silence and music must not.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import av
from faster_whisper import WhisperModel

SR = 16000
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")


def decode_first(path, seconds, skip=0.0):
    rs = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=SR)
    out, total, skipped = [], 0, 0
    with av.open(path) as c:
        for frame in c.decode(audio=0):
            for rf in rs.resample(frame):
                a = rf.to_ndarray().reshape(-1)
                skipped += len(a)
                if skipped < skip * SR:
                    continue
                out.append(a)
                total += len(a)
                if total >= seconds * SR:
                    break
            if total >= seconds * SR:
                break
    return np.concatenate(out).astype(np.float32)[: int(seconds * SR)] if out else np.zeros(0, np.float32)


def music(seconds=4.0):
    """Chord + beat: harmonic, energetic, but not speech."""
    t = np.arange(int(SR * seconds)) / SR
    sig = sum(0.18 * np.sin(2 * np.pi * f * t) for f in (220, 277, 330, 440))
    beat = (np.sin(2 * np.pi * 2 * t) > 0.7).astype(np.float32)
    sig += 0.25 * beat * np.sin(2 * np.pi * 60 * t)
    return sig.astype(np.float32)


def main():
    model = WhisperModel("base", device="cpu", compute_type="int8")
    rng = np.random.default_rng(0)

    cases = [
        ("REAL Arabic speech", decode_first(os.path.join(SAMPLES, "arabic.mp3"), 6.0, skip=12.0), "keep"),
        ("REAL French speech", decode_first(os.path.join(SAMPLES, "french_long.ogg"), 6.0, skip=25.0), "keep"),
        ("REAL German speech", decode_first(os.path.join(SAMPLES, "german.ogg"), 5.0, skip=2.0), "keep"),
        ("silence (tiny noise)", (rng.standard_normal(SR * 4) * 0.0005).astype(np.float32), "drop"),
        ("white noise loud", (rng.standard_normal(SR * 4) * 0.2).astype(np.float32), "drop"),
        ("music chord+beat", music(4.0), "drop"),
        ("pure tone", (0.3 * np.sin(2 * np.pi * 440 * np.arange(SR * 4) / SR)).astype(np.float32), "drop"),
    ]

    print(f"{'case':26} {'no_speech':>10} {'logprob':>9} {'compress':>9}  want")
    print("-" * 72)
    for label, audio, want in cases:
        if len(audio) == 0:
            print(f"{label:26} MISSING")
            continue
        peak = float(np.max(np.abs(audio)))
        a = np.clip(audio / max(peak, 0.05) * 0.95, -1, 1).astype(np.float32)
        segs, info = model.transcribe(
            a, task="translate", beam_size=1, temperature=0.0,
            condition_on_previous_text=False, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 200},
            without_timestamps=True,
        )
        segs = list(segs)
        if not segs:
            print(f"{label:26} {'-':>10} {'-':>9} {'-':>9}  {want}  (vad={info.duration_after_vad:.2f} no segments)")
            continue
        for s in segs:
            print(f"{label:26} {s.no_speech_prob:10.3f} {s.avg_logprob:9.3f} "
                  f"{s.compression_ratio:9.2f}  {want}  (vad={info.duration_after_vad:.2f})")
            print(f"    -> {s.text.strip()[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
