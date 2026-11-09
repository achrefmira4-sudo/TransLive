"""Smoke tests for the parts that can run without Windows audio hardware.

Run:  python -m pytest tests/ -q      (or)      python tests/test_core.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.audio import Resampler
from app.translator import is_hallucination
from app.vad import Segmenter

SR = 16000


def _speech(seconds: float, amp: float = 0.25) -> np.ndarray:
    """Buzzy speech-like signal (not a pure tone, so it has real energy)."""
    t = np.arange(int(SR * seconds)) / SR
    sig = (
        0.5 * np.sin(2 * np.pi * 180 * t)
        + 0.3 * np.sin(2 * np.pi * 420 * t)
        + 0.2 * np.random.default_rng(0).standard_normal(len(t))
    )
    return (sig * amp).astype(np.float32)


def _silence(seconds: float, amp: float = 0.0005) -> np.ndarray:
    n = int(SR * seconds)
    return (np.random.default_rng(1).standard_normal(n) * amp).astype(np.float32)


def test_resampler_48k_to_16k():
    r = Resampler(48000, 16000)
    out = r(np.zeros(4800, dtype=np.float32))
    assert abs(len(out) - 1600) <= 2, len(out)
    assert out.dtype == np.float32


def test_resampler_441k_to_16k():
    r = Resampler(44100, 16000)
    out = r(np.zeros(4410, dtype=np.float32))
    assert abs(len(out) - 1600) <= 2, len(out)


def test_resampler_passthrough():
    r = Resampler(16000, 16000)
    x = _speech(0.1)
    assert len(r(x)) == len(x)


def test_segments_one_utterance():
    seg = Segmenter.from_speed("balanced")
    out = []
    out += seg.push(_silence(0.5))
    out += seg.push(_speech(1.5))
    out += seg.push(_silence(1.2))
    assert len(out) == 1, f"expected 1 utterance, got {len(out)}"
    # 1.5 s speech + preroll + trailing silence, roughly
    assert 1.3 < len(out[0]) / SR < 3.2, len(out[0]) / SR


def test_segments_two_utterances():
    seg = Segmenter.from_speed("balanced")
    out = []
    out += seg.push(_silence(0.4))
    out += seg.push(_speech(1.0))
    out += seg.push(_silence(1.0))
    out += seg.push(_speech(1.0))
    out += seg.push(_silence(1.0))
    assert len(out) == 2, f"expected 2 utterances, got {len(out)}"


def test_silence_produces_nothing():
    seg = Segmenter.from_speed("balanced")
    out = seg.push(_silence(6.0))
    assert out == [], f"silence triggered {len(out)} segments"


def test_short_click_ignored():
    seg = Segmenter.from_speed("balanced")
    out = []
    out += seg.push(_silence(0.5))
    out += seg.push(_speech(0.05))  # too short to be a word
    out += seg.push(_silence(1.0))
    assert out == [], "a 50 ms click should not become an utterance"


def test_long_monologue_is_split():
    seg = Segmenter.from_speed("fast")  # max 8 s
    out = seg.push(_speech(20.0))
    assert len(out) >= 2, f"20 s of speech should be split, got {len(out)}"
    assert all(len(o) / SR <= 8.2 for o in out)


def test_flush_returns_pending():
    seg = Segmenter.from_speed("balanced")
    seg.push(_speech(1.0))
    tail = seg.flush()
    assert tail is not None and len(tail) / SR >= 0.9


def test_pause_blocks_audio():
    seg = Segmenter.from_speed("balanced")
    seg.set_paused(True)
    assert seg.push(_speech(3.0)) == []
    seg.set_paused(False)
    # grace period after unpausing still blocks briefly
    assert seg.push(_speech(0.1)) == []


def test_hallucination_filter():
    for bad in ["Thank you.", "thanks for watching", "you", "Bye bye!",
                "you you you you", "...", "  ", "Mm."]:
        assert is_hallucination(bad), f"should reject: {bad!r}"
    for good in ["Hello, how are you today?",
                 "I will meet you at the station at five.",
                 "Thanks for helping me with the project yesterday."]:
        assert not is_hallucination(good), f"should accept: {good!r}"


def test_speed_presets_differ():
    fast = Segmenter.from_speed("fast")
    accurate = Segmenter.from_speed("accurate")
    assert fast.end_frames < accurate.end_frames
    assert fast.max_frames < accurate.max_frames


def _run_all() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
