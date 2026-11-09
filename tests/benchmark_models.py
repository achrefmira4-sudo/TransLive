"""Benchmark model sizes on real Arabic + French speech (accuracy and speed)."""
from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import av

from app.translator import Translator
from app.vad import Segmenter

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
SR = 16000


def decode_first_seconds(path: str, seconds: float) -> np.ndarray:
    resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=SR)
    out = []
    total = 0
    with av.open(path) as container:
        for frame in container.decode(audio=0):
            for rf in resampler.resample(frame):
                arr = rf.to_ndarray().reshape(-1)
                out.append(arr)
                total += len(arr)
                if total >= seconds * SR:
                    break
            if total >= seconds * SR:
                break
    if not out:
        return np.zeros(0, np.float32)
    a = np.concatenate(out).astype(np.float32)
    return a[: int(seconds * SR)]


def segment(audio: np.ndarray):
    seg = Segmenter.from_speed("balanced")
    utts = []
    for i in range(0, len(audio), 1600):
        utts += seg.push(audio[i:i + 1600])
    tail = seg.flush()
    if tail is not None:
        utts.append(tail)
    return utts


def main() -> int:
    cases = []
    for label, fname, expect in [
        ("Arabic (Moroccan narrator)", "arabic.mp3", "ar"),
        ("French (encyclopedia)", "french_long.ogg", "fr"),
    ]:
        p = os.path.join(SAMPLES, fname)
        if os.path.exists(p):
            cases.append((label, p, expect))
    if not cases:
        print("No samples found.")
        return 1

    prepared = []
    for label, path, expect in cases:
        audio = decode_first_seconds(path, 30.0)
        utts = segment(audio)
        # Keep the first 4 utterances to bound runtime.
        utts = [u for u in utts if len(u) / SR > 0.8][:4]
        prepared.append((label, expect, utts))
        print(f"{label}: {len(audio)/SR:.1f}s decoded, "
              f"{len(utts)} utterances used, "
              f"{sum(len(u) for u in utts)/SR:.1f}s of speech")

    for model_size in ["base", "small"]:
        print(f"\n{'='*66}\nMODEL: {model_size}\n{'='*66}")
        t0 = time.time()
        tr = Translator(model_size=model_size, device="cpu", compute_type="int8")
        tr.load()
        print(f"load time: {time.time()-t0:.1f}s")

        for label, expect, utts in prepared:
            print(f"\n--- {label} (expect {expect}) ---")
            total_audio = 0.0
            total_time = 0.0
            langs = []
            for u in utts:
                t1 = time.time()
                r = tr.process(u, want_original=True)
                dt = time.time() - t1
                total_audio += len(u) / SR
                total_time += dt
                if r is None:
                    print("   (filtered)")
                    continue
                langs.append(r.language)
                print(f"   [{r.language} {r.probability*100:.0f}%] {dt:.2f}s")
                print(f"     orig: {r.original[:110]}")
                print(f"     EN  : {r.english[:110]}")
            if total_audio > 0:
                rtf = total_time / total_audio
                print(f"   speed: {total_time:.1f}s compute / {total_audio:.1f}s audio "
                      f"= {rtf:.2f}x realtime ({'OK' if rtf < 1 else 'TOO SLOW'})")
                print(f"   languages detected: {langs} (expected all '{expect}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
