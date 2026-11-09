"""Check whether tab contents are clipped at the default window size."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import main as app_main


def walk(w, depth=0):
    for c in w.winfo_children():
        yield c, depth
        yield from walk(c, depth + 1)


def main():
    root = tk.Tk()
    app = app_main.App(root)
    root.update_idletasks()
    root.update()
    H = root.winfo_height()
    W = root.winfo_width()
    print(f"window {W}x{H}, root requests {root.winfo_reqwidth()}x{root.winfo_reqheight()}")

    nb = app.notebook
    for idx, name in ((0, "Live"), (1, "Settings"), (2, "Help")):
        nb.select(idx)
        root.update_idletasks()
        root.update()
        tab = nb.nametowidget(nb.tabs()[idx])
        clipped, offscreen = [], []
        for w, _d in walk(tab):
            if not w.winfo_ismapped():
                continue
            ry = w.winfo_rooty() - root.winfo_rooty()
            rx = w.winfo_rootx() - root.winfo_rootx()
            hh, ww = w.winfo_height(), w.winfo_width()
            label = ""
            try:
                label = str(w.cget("text"))[:42]
            except Exception:
                label = w.winfo_class()
            if ry + hh > H - 50:
                clipped.append((ry, hh, label))
            if rx + ww > W:
                offscreen.append((rx, ww, label))
        print(f"\n--- {name} tab ---")
        print(f"  tab req height: {tab.winfo_reqheight()}, allotted: {tab.winfo_height()}")
        print(f"  bottom-clipped widgets: {len(clipped)}")
        for ry, hh, label in clipped[-8:]:
            print(f"     y={ry} h={hh} :: {label}")
        print(f"  right-clipped widgets: {len(offscreen)}")
        for rx, ww, label in offscreen[:5]:
            print(f"     x={rx} w={ww} :: {label}")

    # Is the last settings control reachable?
    nb.select(1)
    root.update_idletasks(); root.update()
    try:
        w = app.rate_spin if hasattr(app, "rate_spin") else None
    except Exception:
        w = None
    print("\nSettings tail widgets (speech rate / note):")
    tab = nb.nametowidget(nb.tabs()[1])
    for c, _ in walk(tab):
        try:
            t = str(c.cget("text"))
        except Exception:
            continue
        if "Settings apply" in t or "Speech rate" in t:
            ry = c.winfo_rooty() - root.winfo_rooty()
            print(f"   '{t[:45]}' y={ry} mapped={bool(c.winfo_ismapped())} "
                  f"visible={ry + c.winfo_height() <= H}")
    root.destroy()


if __name__ == "__main__":
    main()
