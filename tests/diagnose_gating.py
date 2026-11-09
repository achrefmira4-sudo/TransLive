"""Print raw Whisper segment stats to see why real speech is being gated out."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import av
from faster_whisper import WhisperModel

from app.vad import Segmenter

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
SR = 16000


def decode_first(path, seconds):
    rs = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=SR)
    out, total = [], 0
    with av.open(path) as c:
        for frame in c.decode(audio=0):
            for rf in rs.resample(frame):
                a = rf.to_ndarray().reshape(-1)
                out.append(a)
                total += len(a)
                if total >= seconds * SR:
                    break
            if total >= seconds * SR:
                break
    return np.concatenate(out).astype(np.float32)[: int(seconds * SR)]


def segment(audio):
    seg = Segmenter.from_speed("balanced")
    utts = []
    for i in range(0, len(audio), 1600):
        utts += seg.push(audio[i:i + 1600])
    t = seg.flush()
    if t is not None:
        utts.append(t)
    return utts


def main():
    model = WhisperModel("base", device="cpu", compute_type="int8")
    path = os.path.join(SAMPLES, "arabic.mp3")
    audio = decode_first(path, 30.0)
    utts = [u for u in segment(audio) if len(u) / SR > 0.8][:4]

    for lang in ["ar", None]:
        print(f"\n{'='*70}\nforced language = {lang}\n{'='*70}")
        for i, u in enumerate(utts):
            peak = float(np.max(np.abs(u)))
            a = np.clip(u / max(peak, 0.05) * 0.95, -1, 1).astype(np.float32)
            segs, info = model.transcribe(
                a, task="translate", language=lang, beam_size=1,
                temperature=0.0, condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 200},
                without_timestamps=True,
            )
            segs = list(segs)
            print(f"\n utterance {i}: {len(u)/SR:.2f}s peak={peak:.3f} "
                  f"detected={info.language} p={info.language_probability:.2f} "
                  f"dur_after_vad={info.duration_after_vad:.2f}")
            if not segs:
                print("   NO SEGMENTS RETURNED")
            for s in segs:
                print(f"   no_speech={s.no_speech_prob:.3f} "
                      f"logprob={s.avg_logprob:.3f} "
                      f"compress={s.compression_ratio:.2f}")
                print(f"     text: {s.text.strip()[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
