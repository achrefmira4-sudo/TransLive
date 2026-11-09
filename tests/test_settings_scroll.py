"""
Settings must be fully reachable on a small window. The tab requests more
height than the notebook gives it, so it lives in a scrollable canvas. This
verifies the scroll region, the width sync and that the LAST row can actually
be brought into view -- not just that a scrollbar exists.

Run:  xvfb-run -a python3 tests/test_settings_scroll.py
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

RESULTS = []


def check(label, cond, detail=""):
    RESULTS.append((label, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))


def deepest(frame):
    """The last widget in the settings body, by vertical position."""
    best, best_bottom = None, -1
    stack = [frame]
    while stack:
        w = stack.pop()
        for c in w.winfo_children():
            stack.append(c)
            b = c.winfo_y() + c.winfo_reqheight()
            if b > best_bottom and c.winfo_reqheight() > 1:
                best, best_bottom = c, b
    return best


def run():
    # Windows has no $DISPLAY but may still have a desktop; only Linux needs it.
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        print("No DISPLAY; run under: xvfb-run -a python3 tests/test_settings_scroll.py")
        return 1

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        # CI services can run without an interactive desktop. Skip loudly
        # rather than failing the build; these run locally under xvfb-run.
        print(f"SKIP: no usable display for real-window tests ({exc})")
        return 0
    app = main.App(root)
    root.geometry("900x680+0+0")   # override any saved geometry
    app.notebook.select(1)         # Settings
    root.update_idletasks()
    root.update()

    try:
        canvas = app.settings_canvas
        wrap = canvas.nametowidget(canvas.itemcget(canvas.find_all()[0], "window"))
        root.update_idletasks()

        ch, wh = canvas.winfo_height(), wrap.winfo_reqheight()
        print("\n--- settings content vs viewport ---")
        print(f"  canvas viewport h={ch}, content h={wh}")
        check("settings content is taller than the viewport (scroll needed)",
              wh > ch, f"{wh} > {ch}")

        print("\n--- scroll region covers the whole body ---")
        region = canvas.cget("scrollregion")
        parts = [float(v) for v in str(region).split()]
        check("scroll region is set", len(parts) == 4, str(region))
        check("scroll region covers full content height",
              len(parts) == 4 and parts[3] >= wh - 2, f"{parts} vs {wh}")

        print("\n--- body width tracks the canvas (no horizontal clipping) ---")
        check("body width == canvas width",
              abs(wrap.winfo_width() - canvas.winfo_width()) <= 2,
              f"body={wrap.winfo_width()} canvas={canvas.winfo_width()}")

        print("\n--- scrolling to the bottom reveals the last row ---")
        last = deepest(wrap)
        check("found a last row to test", last is not None)
        canvas.yview_moveto(1.0)
        root.update_idletasks()
        root.update()
        top_frac, bot_frac = canvas.yview()
        check("canvas actually scrolled to the bottom",
              bot_frac > 0.999, f"yview={canvas.yview()}")
        # Position of the last row relative to the canvas viewport.
        ly = last.winfo_rooty() - canvas.winfo_rooty()
        lh = last.winfo_height()
        check("last row is inside the viewport after scrolling",
              ly >= -2 and ly + lh <= canvas.winfo_height() + 2,
              f"row y={ly} h={lh} viewport={canvas.winfo_height()}")

        print("\n--- scrolling back to the top works ---")
        canvas.yview_moveto(0.0)
        root.update_idletasks()
        root.update()
        check("canvas returned to the top", canvas.yview()[0] < 0.001,
              f"yview={canvas.yview()}")

        print("\n--- Start button stays visible on the Settings tab ---")
        b = app.start_btn
        y = b.winfo_rooty() - root.winfo_rooty()
        check("Start button visible while on Settings",
              b.winfo_ismapped() and b.winfo_height() >= 30
              and y + b.winfo_height() <= root.winfo_height(),
              f"y={y} h={b.winfo_height()}")

        print("\n--- a tall window shows everything without scrolling ---")
        root.geometry("1000x900+0+0")
        root.update_idletasks()
        root.update()
        ch, wh = canvas.winfo_height(), wrap.winfo_reqheight()
        check("content fits when the window is tall enough", wh <= ch + 2,
              f"content={wh} viewport={ch}")
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(failed)} failure(s)")
    for n in failed:
        print(f"  - {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
