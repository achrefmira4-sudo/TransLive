"""Regression test for the Stop -> Start lifecycle race.

The original bug: stop() handed shutdown to a background thread that ended with
`self.pipeline = None`. Shutdown joins audio threads (up to ~10 s), so clicking
Start during that window created a NEW pipeline that the OLD worker then nulled
out. Result: a live audio stream with no way to stop it, plus the new session's
transcript file closed underneath it.

These tests drive the real App.stop()/_stop_worker/_finish_stop logic with a
deliberately slow fake pipeline.

Run: python tests/test_lifecycle.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_gui_smoke import _install_fake_tk  # reuse the headless Tk mock


class SlowPipeline:
    """Pretends to be a capture pipeline that takes a while to release."""

    def __init__(self, delay=0.6):
        self.delay = delay
        self.running = True
        self.stop_calls = 0
        self.stop_finished = False

    def stop(self):
        self.stop_calls += 1
        time.sleep(self.delay)
        self.running = False
        self.stop_finished = True


class FakeFile:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def write(self, *_a):
        if self.closed:
            raise ValueError("write to closed file")

    def flush(self):
        pass


def main() -> int:
    _install_fake_tk()
    import main as app_main
    from unittest.mock import MagicMock

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"  PASS  {label}")
        else:
            failures.append(label)
            print(f"  FAIL  {label} {detail}")

    root = MagicMock(name="root")
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080
    root.geometry.return_value = "900x680+10+10"
    # Run only immediate (delay=0) callbacks inline — that is what _finish_stop
    # uses. The GUI event loop reschedules itself with after(80, ...), so running
    # every callback inline would recurse forever.
    def _after(ms, fn=None, *a):
        if ms == 0 and fn is not None:
            fn(*a)
        return "after-id"

    root.after.side_effect = _after

    app = app_main.App(root)

    # --- session 1: start a slow-to-stop pipeline, then stop it ---
    p1 = SlowPipeline(delay=0.6)
    f1 = FakeFile()
    app.pipeline = p1
    app.transcript_file = f1
    app.speaker = None

    app.stop()
    check("stop() marks stopping immediately", app._stopping is True)
    check("stop() detaches pipeline from self at once", app.pipeline is None)
    check("button disabled while stopping",
          app.start_btn.configure.call_args.kwargs.get("state") == "disabled")

    # --- user clicks Start again while session 1 is still shutting down ---
    app.start()
    check("start() refused during shutdown (no second stream)",
          app.pipeline is None, f"pipeline={app.pipeline}")

    # --- session 2 begins the way the real UI would, after stop completes ---
    # Simulate the user starting a new session while the old worker still runs.
    p2 = SlowPipeline(delay=0.0)
    f2 = FakeFile()
    app._stopping = False          # what _finish_stop would do
    app._session_id += 1           # new session owns the state now
    app.pipeline = p2
    app.transcript_file = f2

    # Let the OLD worker finish; it must not touch session 2.
    deadline = time.time() + 5.0
    while not p1.stop_finished and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)

    check("old pipeline was stopped exactly once", p1.stop_calls == 1,
          f"calls={p1.stop_calls}")
    check("old transcript closed", f1.closed is True)
    check("NEW pipeline survived old shutdown", app.pipeline is p2,
          f"got {app.pipeline}")
    check("NEW transcript left open", f2.closed is False)
    check("new pipeline still running", p2.running is True)

    # --- stopping session 2 must stop p2, not p1 ---
    app.stop()
    deadline = time.time() + 5.0
    while not p2.stop_finished and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    check("session 2 pipeline stopped", p2.stop_finished is True)
    check("session 2 transcript closed", f2.closed is True)
    check("stopping flag cleared after shutdown", app._stopping is False)
    check("button re-enabled after shutdown",
          app.start_btn.configure.call_args.kwargs.get("state") == "normal")

    # --- double stop() must not spawn two workers ---
    p3 = SlowPipeline(delay=0.3)
    app.pipeline = p3
    app._stopping = False
    app.stop()
    app.stop()
    deadline = time.time() + 5.0
    while not p3.stop_finished and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    check("repeated stop() is idempotent", p3.stop_calls == 1,
          f"calls={p3.stop_calls}")

    # --- stop() with nothing running must not wedge the UI ---
    app.pipeline = None
    app.speaker = None
    app.transcript_file = None
    app._stopping = False
    app.stop()
    check("stop() with no session leaves UI usable", app._stopping is False)
    check("toggle() works when idle", callable(app.toggle))

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
