"""Headless smoke test for the Tk GUI wiring.

There is no display in CI, so Tk is replaced with mocks that return real values
where the code needs numbers/strings. This still executes all of our own wiring
code and catches typos, missing attributes and bad call signatures — the class
of bug that would otherwise only appear when a user launches the app.

Run: python tests/test_gui_smoke.py
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Var:
    def __init__(self, value=None, **kw):
        self._v = value if value is not None else kw.get("value")

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


def _install_fake_tk():
    tk = MagicMock(name="tkinter")
    tk.StringVar = lambda value=None, **kw: _Var(value if value is not None else "")
    tk.IntVar = lambda value=None, **kw: _Var(value if value is not None else 0)
    tk.BooleanVar = lambda value=None, **kw: _Var(bool(value))
    tk.Tk = MagicMock
    fake = types.ModuleType("tkinter")
    for attr in ("Frame", "Label", "Button", "Text", "Canvas", "Toplevel",
                 "StringVar", "IntVar", "BooleanVar", "Tk", "END"):
        setattr(fake, attr, getattr(tk, attr))
    fake.Tk = tk.Tk
    fake.StringVar = tk.StringVar
    fake.IntVar = tk.IntVar
    fake.BooleanVar = tk.BooleanVar
    fake.END = "end"
    ttk = MagicMock(name="ttk")

    # Real Tk Combobox.current() returns an int (-1 when nothing is selected).
    # A bare MagicMock here would break `0 <= idx < len(devices)` in app code.
    def _combobox(*a, **kw):
        cb = MagicMock(name="Combobox")
        cb.current.return_value = -1
        return cb

    ttk.Combobox = _combobox
    filedialog = MagicMock(name="filedialog")
    messagebox = MagicMock(name="messagebox")
    fake.ttk = ttk
    fake.filedialog = filedialog
    fake.messagebox = messagebox
    sys.modules["tkinter"] = fake
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.filedialog"] = filedialog
    sys.modules["tkinter.messagebox"] = messagebox
    return fake, messagebox


def main() -> int:
    fake_tk, messagebox = _install_fake_tk()

    import main as app_main
    from app.pipeline import Event

    root = MagicMock(name="root")
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080
    root.geometry.return_value = "900x680+10+10"

    failures = []

    def step(label, fn):
        try:
            fn()
            print(f"  PASS  {label}")
        except Exception as exc:
            failures.append(label)
            print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")

    app_holder = {}

    def build():
        app = app_main.App(root)
        app_holder["app"] = app
        # Overlay geometry must be a real string for JSON settings save.
        app.overlay.win.geometry.return_value = "1100x150+400+800"

    step("App constructs (all tabs, widgets, overlay)", build)
    if "app" not in app_holder:
        print("\nCannot continue without an App instance.")
        return 1
    app = app_holder["app"]

    step("model note renders", app._model_note)
    step("overlay settings apply", app._apply_overlay)
    step("device refresh with no hardware", app.refresh_devices)

    def feed_events():
        app.events.put(Event("status", text="Model ready on CPU."))
        app.events.put(Event("level", level=0.05))
        app.events.put(Event("translation", text="Hello, how are you?",
                             original="Hallo, wie geht es dir?",
                             language="de", probability=0.98))
        app.events.put(Event("translation", text="مرحبا translated",
                             language="ar", probability=0.95))
        app.events.put(Event("perf", rtf=3.1, text="Model too slow."))
        app.events.put(Event("perf", dropped=2))
        app.events.put(Event("error", text="test error"))
        app.events.put(Event("stopped"))
        app._drain_events()

    step("event loop handles every event kind", feed_events)

    def check_transcript():
        assert len(app.transcript_lines) == 2, app.transcript_lines
        assert "Hello, how are you?" in app.transcript_lines[0]
        assert "German" in app.transcript_lines[0], app.transcript_lines[0]
        assert "Arabic" in app.transcript_lines[1], app.transcript_lines[1]

    step("translations recorded with language names", check_transcript)

    def check_start_guard():
        # No audio hardware here -> must show an error, not crash.
        app.start()
        assert messagebox.showerror.called, "expected a friendly error dialog"

    step("start with no device shows dialog (no crash)", check_start_guard)

    step("settings round-trip to disk", app._save_settings)

    def check_settings_saved():
        import json
        from app.config import app_dir
        p = app_dir() / "settings.json"
        assert p.exists(), "settings.json not written"
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "model_size" in data and "language" in data, data
        assert isinstance(data["overlay_geometry"], str), data["overlay_geometry"]

    step("settings.json is valid and complete", check_settings_saved)
    step("clear transcript", app.clear_transcript)
    step("clean shutdown", app.on_close)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
