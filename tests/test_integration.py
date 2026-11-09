"""End-to-end test: real non-English speech -> VAD -> Whisper -> English.

Downloads the tiny model on first run. Skips gracefully if offline.
Run:  python tests/test_integration.py
"""
from __future__ import annotations

import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.audio import Resampler
from app.translator import Translator, HAS_WHISPER
from app.vad import Segmenter

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
SR = 16000


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    a = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        a = (a - 128.0) / 128.0
    else:
        a /= float(2 ** (8 * width - 1))
    if ch > 1:
        a = a[: (len(a) // ch) * ch].reshape(-1, ch).mean(axis=1)
    return Resampler(rate, SR)(a)


def load_any(path: str) -> np.ndarray:
    if path.lower().endswith(".wav"):
        return load_wav(path)
    import av  # bundled with faster-whisper

    resampler = av.audio.resampler.AudioResampler(
        format="flt", layout="mono", rate=SR
    )
    out = []
    with av.open(path) as container:
        for frame in container.decode(audio=0):
            for rf in resampler.resample(frame):
                out.append(rf.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, np.float32)


def main() -> int:
    if not HAS_WHISPER:
        print("SKIP: faster-whisper not installed")
        return 0

    french = os.path.join(SAMPLES, "french.wav")
    german = os.path.join(SAMPLES, "german.ogg")
    if not os.path.exists(french):
        print("SKIP: run tests/samples/fetch_samples.py first")
        return 0

    print("Loading tiny model (downloads on first run)...")
    tr = Translator(model_size="tiny", device="cpu", compute_type="int8",
                    on_status=lambda m: print("  [status]", m))
    try:
        tr.load()
    except Exception as exc:
        print(f"SKIP: could not load model ({exc})")
        return 0

    cases = [("French", french, "fr")]
    if os.path.exists(german):
        cases.append(("German", german, "de"))

    failures = 0
    for label, path, expected_lang in cases:
        print(f"\n=== {label}: {os.path.basename(path)} ===")
        audio = load_any(path)
        print(f"  decoded {len(audio)/SR:.2f}s @ {SR}Hz, peak={np.max(np.abs(audio)):.3f}")

        # Feed through the real segmentation path, in streaming-sized chunks.
        seg = Segmenter.from_speed("balanced")
        utterances = []
        chunk = 1600  # 100 ms, like the live capture loop
        for i in range(0, len(audio), chunk):
            utterances += seg.push(audio[i:i + chunk])
        tail = seg.flush()
        if tail is not None:
            utterances.append(tail)
        if not utterances:
            utterances = [audio]
        print(f"  VAD produced {len(utterances)} utterance(s)")

        got_text = False
        for u in utterances:
            result = tr.process(u, want_original=True)
            if result is None:
                print("  (filtered as silence/hallucination)")
                continue
            got_text = True
            print(f"  detected language : {result.language} "
                  f"({result.probability*100:.0f}%)")
            print(f"  original          : {result.original}")
            print(f"  ENGLISH           : {result.english}")
            if result.language != expected_lang:
                print(f"  WARN: expected {expected_lang}, got {result.language}")
            ascii_ratio = sum(c.isascii() for c in result.english) / max(1, len(result.english))
            if ascii_ratio < 0.8:
                print("  FAIL: output does not look like English")
                failures += 1
        if not got_text:
            print("  FAIL: no translation produced")
            failures += 1

    print(f"\n{'FAILED' if failures else 'PASSED'}: {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
