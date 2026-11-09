"""
The #1 wrong setting: picking a MICROPHONE when you want to translate a
Discord/phone call. A mic hears the room, not the remote participants.

The app must say so at selection time and again at Start, and must not crash
while doing it (a missing colour constant previously raised NameError exactly
when a mic was selected).

Run:  xvfb-run -a python3 tests/test_source_warning.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from app.audio import Device  # noqa: E402

RESULTS = []


def check(label, cond, detail=""):
    RESULTS.append((label, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))


def build_app():
    import tkinter as tk
    root = tk.Tk()          # TclError is handled by the caller
    app = main.App(root)
    app.root.update_idletasks()
    app.root.update()
    return app


def main_test():
    # Windows has no $DISPLAY but may still have a desktop; only Linux needs it.
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        print("No DISPLAY; run under: xvfb-run -a python3 tests/test_source_warning.py")
        return 1

    import tkinter as tk
    try:
        app = build_app()
    except tk.TclError as exc:
        print(f"SKIP: no usable display for real-window tests ({exc})")
        return 0
    try:
        loopback = Device(index=1, name="Speakers (Realtek Audio)",
                          kind="loopback", channels=2, rate=48000)
        mic = Device(index=2, name="Microphone Array (Realtek(R) Audio)",
                     kind="mic", channels=2, rate=48000)
        app.devices = [loopback, mic]
        app.device_combo.configure(values=[d.label for d in app.devices])

        print("\n--- selecting a MICROPHONE warns the user ---")
        app.device_combo.current(1)
        app._on_device_change()
        app.root.update_idletasks()
        text = app.source_hint.cget("text")
        check("hint warns about microphone", "MICROPHONE" in text, f"-> {text[:70]}")
        check("hint explains it misses the call", "not capture" in text.lower())
        check("hint points to the right source", "What you hear" in text)
        check("hint is amber, not muted", str(app.source_hint.cget("foreground")) == main.AMBER)

        print("\n--- selecting a LOOPBACK source reassures the user ---")
        app.device_combo.current(0)
        app._on_device_change()
        app.root.update_idletasks()
        text = app.source_hint.cget("text")
        check("hint confirms loopback captures the PC",
              "everything playing" in text.lower(), f"-> {text[:70]}")
        check("no leftover microphone warning", "MICROPHONE" not in text)

        print("\n--- Start on a microphone logs a visible explanation ---")
        app._append_system("marker")
        before = app.transcript.get("1.0", "end-1c")
        app.device_combo.current(1)
        app._on_device_change()
        # Call the SAME method the Start handler calls, so deleting the real
        # warning would fail this test instead of silently passing.
        app.explain_microphone()
        after = app.transcript.get("1.0", "end-1c")
        check("start-time mic explanation is logged",
              "MICROPHONE" in after and "MICROPHONE" not in before)
        # And confirm the real Start handler is wired to that method.
        src = open(main.__file__, encoding="utf-8").read()
        check("Start handler calls the warning for mic sources",
              'if device.kind == "mic":' in src
              and "self.explain_microphone()" in src)

        print("\n--- the Start button is never hidden by these changes ---")
        app.root.geometry("900x680")
        app.root.update_idletasks()
        app.root.update()
        b = app.start_btn
        y, h = b.winfo_rooty() - app.root.winfo_rooty(), b.winfo_height()
        win_h = app.root.winfo_height()
        check("Start button visible at 900x680",
              b.winfo_ismapped() and h >= 30 and y + h <= win_h,
              f"y={y} h={h} window_h={win_h}")

        for geo in ("900x600", "820x600"):
            app.root.geometry(geo)
            app.root.update_idletasks()
            app.root.update()
            y = b.winfo_rooty() - app.root.winfo_rooty()
            h = b.winfo_height()
            check(f"Start button visible at {geo}",
                  b.winfo_ismapped() and h >= 30 and y + h <= app.root.winfo_height(),
                  f"y={y} h={h}")
    finally:
        try:
            app.root.destroy()
        except Exception:
            pass

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(failed)} failure(s)")
    for n in failed:
        print(f"  - {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main_test())
