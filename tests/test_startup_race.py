"""Regression test for the false "Skipped phrases" warning at startup.

User-reported log showed:

    Listening to: Microphone Array (Realtek(R) Audio)
    ⚠ Skipped 1 phrase(s) to stay live. Use a smaller model for this PC.
    Loading tiny model on cuda (float16)… first run downloads the model.

The skip was reported BEFORE the model finished loading, and the user was
already on `tiny` (the smallest model) with cuda — so the advice was impossible
to follow. Cause: the segmenter queued speech during model load, overflowing the
2-utterance backlog.

Run: python tests/test_startup_race.py
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline import Pipeline
from app.translator import TranslationResult
from app.vad import Segmenter

SR = 16000
failures: list[str] = []


def check(label, ok, detail=""):
    if ok:
        print(f"  PASS  {label}")
    else:
        failures.append(label)
        print(f"  FAIL  {label} {detail}")


def speech(seconds=1.0):
    t = np.arange(int(SR * seconds)) / SR
    return (0.3 * np.sin(2 * np.pi * 180 * t)
            + 0.15 * np.random.default_rng(0).standard_normal(len(t))).astype(np.float32)


def silence(seconds=1.0):
    return np.zeros(int(SR * seconds), np.float32)


class SlowLoadTranslator:
    """Model that takes a while to load, like a first-run download."""

    def __init__(self, load_delay=0.8, infer_delay=0.01,
                 model_size="tiny", actual_device="cuda"):
        self.load_delay = load_delay
        self.infer_delay = infer_delay
        self.model_size = model_size
        self.actual_device = actual_device
        self.language = None
        self.calls = 0
        self.loaded_at = None

    def reset_session(self):
        pass

    def load(self):
        time.sleep(self.load_delay)
        self.loaded_at = time.monotonic()

    def process(self, audio, want_original=False, skip_english=False):
        self.calls += 1
        time.sleep(self.infer_delay)
        return TranslationResult(f"phrase {self.calls}", "", "ar", 0.99)


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_no_skips_during_model_load():
    print("\n--- speech arriving while the model loads ---")
    events = queue.Queue(maxsize=4096)
    tr = SlowLoadTranslator(load_delay=0.8)
    p = Pipeline(tr, None, events, speed="fast")

    # Drive the real loops without audio hardware.
    p._segmenter = Segmenter.from_speed("fast")
    p.running = True
    p._work_thread = threading.Thread(target=p._translate_loop, daemon=True)
    p._work_thread.start()

    # Feed lots of speech DURING the load window, exactly like a live mic.
    t_end = time.monotonic() + 0.7
    while time.monotonic() < t_end:
        for u in p._segmenter.push(speech(1.0)):
            p._enqueue(u)
        for u in p._segmenter.push(silence(0.8)):
            p._enqueue(u)

    pre_load = [e for e in drain(events) if e.kind == "perf" and e.dropped]
    check("no 'skipped phrases' emitted before the model is ready",
          not pre_load, f"got {len(pre_load)} notices")
    check("nothing was queued during load", p._dropped == 0,
          f"dropped={p._dropped}")

    time.sleep(0.5)
    p._stop.set()
    p._work_thread.join(timeout=5)
    check("worker saw zero translated phrases from the load window",
          tr.calls == 0, f"calls={tr.calls}")


def test_ready_gate_opens():
    print("\n--- speech AFTER the model is ready still works ---")
    events = queue.Queue(maxsize=4096)
    tr = SlowLoadTranslator(load_delay=0.2)
    p = Pipeline(tr, None, events, speed="fast")
    p._segmenter = Segmenter.from_speed("fast")
    p.running = True
    p._work_thread = threading.Thread(target=p._translate_loop, daemon=True)
    p._work_thread.start()

    deadline = time.monotonic() + 5
    while not p._ready.is_set() and time.monotonic() < deadline:
        time.sleep(0.02)
    check("ready flag set after load", p._ready.is_set())

    got_ready_msg = any(e.kind == "status" and "Ready" in e.text
                        for e in drain(events))
    check("user is told when it is actually ready", got_ready_msg)

    for u in p._segmenter.push(speech(1.0)):
        p._enqueue(u)
    for u in p._segmenter.push(silence(0.9)):
        p._enqueue(u)
    time.sleep(0.6)
    p._stop.set()
    p._work_thread.join(timeout=5)

    evs = drain(events)
    texts = [e for e in evs if e.kind == "translation"]
    check("real speech after load is translated", len(texts) >= 1,
          f"got {len(texts)}")


def test_advice_matches_settings():
    print("\n--- slow-model advice fits the actual configuration ---")
    events = queue.Queue(maxsize=1024)

    # The user's exact setup: tiny on cuda, original text off.
    tr = SlowLoadTranslator(model_size="tiny", actual_device="cuda")
    p = Pipeline(tr, None, events, want_original=False)
    advice = p._slow_advice(3.0)
    print(f"   tiny+cuda: {advice}")
    check("does not tell a tiny user to pick a smaller model",
          "smaller model" not in advice and "'tiny'" not in advice)
    check("does not tell a cuda user to enable cuda",
          "set Processing to cuda" not in advice)
    check("offers a real next step instead",
          "Response speed" in advice or "GPU may be" in advice)

    # A CPU user on medium should get the classic advice.
    tr2 = SlowLoadTranslator(model_size="medium", actual_device="cpu")
    p2 = Pipeline(tr2, None, events, want_original=True)
    a2 = p2._slow_advice(3.0)
    print(f"   medium+cpu: {a2}")
    check("suggests a smaller model when one exists", "'small'" in a2)
    check("suggests cuda when on cpu", "cuda" in a2)
    check("suggests disabling original text when enabled",
          "original language text" in a2)


def test_first_inference_not_judged():
    print("\n--- CUDA warm-up inference is not treated as 'too slow' ---")
    events = queue.Queue(maxsize=1024)
    tr = SlowLoadTranslator(load_delay=0.0, infer_delay=0.0)
    p = Pipeline(tr, None, events, speed="fast")
    p._ready.set()

    # One very slow first inference relative to audio length.
    tr.infer_delay = 0.5
    p._translate(speech(0.6))
    check("first inference excluded from the RTF history",
          len(p._rtf_history) == 0, f"history={list(p._rtf_history)}")
    warns = [e for e in drain(events) if e.kind == "perf" and e.text]
    check("no slow-model warning from warm-up alone", not warns)


def main():
    test_no_skips_during_model_load()
    test_ready_gate_opens()
    test_advice_matches_settings()
    test_first_inference_not_judged()
    print(f"\n{len(failures)} failure(s)")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
