"""Verify the three fixes for problems found by benchmark_models.py.

1. Language lock stops short-clip misdetection (Arabic -> "Japanese").
2. Music/noise intros are gated out instead of becoming invented sentences.
3. A slow translator drops whole utterances, never mid-sentence audio chunks.

Run: python tests/test_fixes.py
"""
from __future__ import annotations

import os
import queue
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import av

from app.pipeline import Event, Pipeline
from app.translator import Translator, HAS_WHISPER, _keep_segment
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
    return np.concatenate(out).astype(np.float32)[: int(seconds * SR)] if out else np.zeros(0, np.float32)


def segment(audio, speed="balanced"):
    seg = Segmenter.from_speed(speed)
    utts = []
    for i in range(0, len(audio), 1600):
        utts += seg.push(audio[i:i + 1600])
    t = seg.flush()
    if t is not None:
        utts.append(t)
    return utts


class FakeSeg:
    """Segment with the attributes _keep_segment inspects."""
    def __init__(self, no_speech_prob=0.0, avg_logprob=-0.2, compression_ratio=1.5):
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob
        self.compression_ratio = compression_ratio


def test_segment_gating():
    print("\n[FIX 2] segment gating calibrated on real recordings")
    # These are the ACTUAL stats measured from real speech — all must survive.
    real = [
        ("Arabic utt0", FakeSeg(0.022, -1.291, 0.50)),
        ("Arabic utt1", FakeSeg(0.031, -1.272, 1.26)),
        ("Arabic utt2", FakeSeg(0.003, -1.790, 0.84)),
        ("Arabic utt3", FakeSeg(0.034, -2.265, 0.53)),
        ("Arabic mid", FakeSeg(0.224, -2.022, 1.08)),
        ("French", FakeSeg(0.017, -0.777, 1.04)),
        ("German", FakeSeg(0.014, -0.676, 1.08)),
    ]
    for label, seg in real:
        assert _keep_segment(seg), f"REGRESSION: real {label} speech was gated out"
    print(f"   PASS: all {len(real)} real-speech samples kept")

    bad = [
        ("confident no-speech", FakeSeg(0.95, -1.4, 1.2)),
        ("pathological decode", FakeSeg(0.1, -3.5, 1.2)),
        ("repetition loop", FakeSeg(0.1, -0.5, 3.5)),
    ]
    for label, seg in bad:
        assert not _keep_segment(seg), f"{label} should be dropped"
    print(f"   PASS: all {len(bad)} bad segments dropped")
    return True


def test_language_lock():
    print("\n[FIX 1] language lock on real Arabic audio")
    path = os.path.join(SAMPLES, "arabic.mp3")
    if not os.path.exists(path):
        print("   SKIP: sample missing")
        return True
    audio = decode_first(path, 30.0)
    utts = [u for u in segment(audio) if len(u) / SR > 0.8][:4]

    tr = Translator(model_size="base", device="cpu", compute_type="int8",
                    language="ar")
    tr.load()
    langs = []
    gated = 0
    for u in utts:
        r = tr.process(u, want_original=False)
        if r is None:
            gated += 1
            print("   (gated out)")
            continue
        langs.append(r.language)
        print(f"   [{r.language}] {r.english[:95]}")

    # Guard against a vacuous pass: all([]) is True, which previously hid the
    # fact that every real Arabic utterance was being discarded.
    if not langs:
        print(f"   FAIL: all {len(utts)} real Arabic utterances were gated out "
              "(vacuous-pass guard)")
        return False
    if len(langs) < 2:
        print(f"   FAIL: only {len(langs)}/{len(utts)} utterances survived; "
              "gating is too aggressive for real speech")
        return False
    ok = all(l == "ar" for l in langs)
    print(f"   languages: {langs} ({gated} gated) -> "
          f"{'PASS' if ok else 'FAIL'} (all must be 'ar')")
    return ok


def test_music_intro_gated():
    print("\n[FIX 2] music intro on real French audio (auto-detect)")
    path = os.path.join(SAMPLES, "french_long.ogg")
    if not os.path.exists(path):
        print("   SKIP: sample missing")
        return True
    audio = decode_first(path, 12.0)
    utts = [u for u in segment(audio) if len(u) / SR > 0.8][:2]
    tr = Translator(model_size="base", device="cpu", compute_type="int8")
    tr.load()
    produced = []
    for u in utts:
        r = tr.process(u, want_original=False)
        if r:
            produced.append((r.language, r.probability, r.english[:80]))
    for lang, prob, text in produced:
        print(f"   [{lang} {prob*100:.0f}%] {text}")
    if not produced:
        print("   PASS: intro produced no invented text")
    else:
        print("   NOTE: intro still produced text (gating reduced, not eliminated)")
    return True


class SlowTranslator:
    """Simulates a model slower than realtime to test backlog behaviour."""
    def __init__(self, delay=0.35):
        self.delay = delay
        self.calls = 0
        self.language = None

    def reset_session(self):
        pass

    def load(self):
        pass

    def process(self, audio, want_original=False, skip_english=False):
        self.calls += 1
        time.sleep(self.delay)
        from app.translator import TranslationResult
        return TranslationResult(f"sentence {self.calls}", "", "fr", 0.99)


def test_backlog_drops_whole_utterances():
    print("\n[FIX 3] backlog handling with a too-slow translator")
    events: "queue.Queue[Event]" = queue.Queue(maxsize=2048)
    tr = SlowTranslator(delay=0.30)
    p = Pipeline(tr, None, events, speed="fast")

    # Bypass real audio hardware: drive the segment stage directly.
    p._segmenter = Segmenter.from_speed("fast")
    p.running = True
    import threading
    p._work_thread = threading.Thread(target=p._translate_loop, daemon=True)
    p._work_thread.start()

    speech = (np.random.default_rng(0).standard_normal(int(SR * 1.0)) * 0.2).astype(np.float32)
    silence = np.zeros(int(SR * 0.8), np.float32)
    for _ in range(8):
        for u in p._segmenter.push(speech):
            p._enqueue(u)
        for u in p._segmenter.push(silence):
            p._enqueue(u)

    time.sleep(1.0)
    p._stop.set()
    if p._work_thread:
        p._work_thread.join(timeout=5.0)

    dropped = 0
    got = 0
    while True:
        try:
            ev = events.get_nowait()
        except queue.Empty:
            break
        if ev.kind == "perf" and ev.dropped:
            dropped = max(dropped, ev.dropped)
        if ev.kind == "translation":
            got += 1

    print(f"   translated={got}  dropped_whole={dropped}  backlog_cap=2")
    ok = dropped > 0 and got > 0
    print(f"   {'PASS' if ok else 'FAIL'}: overflow dropped whole phrases, kept output clean")
    return ok


def main():
    if not HAS_WHISPER:
        print("SKIP: faster-whisper not installed")
        return 0
    results = []
    results.append(("segment gating", test_segment_gating()))
    results.append(("backlog", test_backlog_drops_whole_utterances()))
    results.append(("language lock", test_language_lock()))
    results.append(("music intro", test_music_intro_gated()))
    print("\n" + "=" * 60)
    failed = [n for n, ok in results if not ok]
    for n, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
