"""Measure real Tk widget geometry to find the invisible Start button."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import main as app_main


def report(app, root, label):
    root.update_idletasks()
    root.update()
    W = root.winfo_width()
    H = root.winfo_height()
    print(f"\n=== {label}: window {W}x{H} ===")
    print(f"  root requested size: {root.winfo_reqwidth()}x{root.winfo_reqheight()}")
    for name, w in (("start_btn", app.start_btn),
                    ("button bar", app.start_btn.master),
                    ("notebook", app.notebook)):
        try:
            hh = w.winfo_height()
            rq = w.winfo_reqheight()
            mapped = bool(w.winfo_ismapped())
            # Position relative to the toplevel
            ry = w.winfo_rooty() - root.winfo_rooty()
            visible = mapped and hh > 1 and ry + hh <= H and ry >= 0
            print(f"  {name:11} y={ry:5} h={hh:4} req_h={rq:4} "
                  f"mapped={mapped} FULLY_VISIBLE={visible}")
        except Exception as e:
            print(f"  {name}: {e}")
    return H


def main():
    root = tk.Tk()
    app = app_main.App(root)
    # expose notebook for measurement
    for child in root.winfo_children():
        if isinstance(child, tk.ttk.Notebook):
            app.notebook = child
    print("minsize:", root.minsize())
    print("geometry:", root.geometry())

    report(app, root, "default 900x680 (Live tab)")

    # Switch to Settings, which is the tallest tab
    app.notebook.select(1)
    report(app, root, "Settings tab selected")

    # Simulate a small window / large font scaling
    root.geometry("900x600")
    report(app, root, "shrunk to 900x600")

    root.geometry("820x600")
    report(app, root, "minsize 820x600")

    root.destroy()


if __name__ == "__main__":
    main()
