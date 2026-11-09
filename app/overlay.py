"""Always-on-top subtitle overlay.

Frameless, semi-transparent, draggable, and optionally click-through so it can
sit over a game or Discord without stealing mouse clicks.
"""
from __future__ import annotations

import sys
import tkinter as tk


class SubtitleOverlay:
    BG = "#101014"
    FG = "#FFFFFF"
    ORIGINAL_FG = "#8AB4F8"

    def __init__(self, root: tk.Tk, font_size: int = 22, lines: int = 2,
                 fade_seconds: int = 8, click_through: bool = False,
                 geometry: str = "") -> None:
        self.root = root
        self.font_size = font_size
        self.max_lines = lines
        self.fade_seconds = fade_seconds
        self.click_through = click_through
        self._fade_job = None
        self._drag_offset = (0, 0)
        self._history: list[tuple[str, str]] = []

        self.win = tk.Toplevel(root)
        self.win.title("Subtitles")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.BG)
        try:
            self.win.attributes("-alpha", 0.86)
        except Exception:
            pass

        self.frame = tk.Frame(self.win, bg=self.BG, padx=18, pady=12)
        self.frame.pack(fill="both", expand=True)

        self.label = tk.Label(
            self.frame, text="Subtitles will appear here…",
            font=("Segoe UI", self.font_size, "bold"),
            fg=self.FG, bg=self.BG, justify="center", wraplength=1000,
        )
        self.label.pack(fill="both", expand=True)

        self.original_label = tk.Label(
            self.frame, text="", font=("Segoe UI", max(11, self.font_size - 7)),
            fg=self.ORIGINAL_FG, bg=self.BG, justify="center", wraplength=1000,
        )
        self.original_label.pack(fill="x", pady=(6, 0))

        width = min(1100, int(root.winfo_screenwidth() * 0.75))
        self.width = width
        if geometry:
            try:
                self.win.geometry(geometry)
            except Exception:
                self._center_bottom(width)
        else:
            self._center_bottom(width)

        for w in (self.win, self.frame, self.label, self.original_label):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<Double-Button-1>", self._toggle_click_through)

        self.win.withdraw()

    def _center_bottom(self, width: int) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, sh - 220)
        self.win.geometry(f"{width}x150+{x}+{y}")

    # ---- dragging ----
    def _start_drag(self, event) -> None:
        self._drag_offset = (event.x_root - self.win.winfo_x(),
                             event.y_root - self.win.winfo_y())

    def _on_drag(self, event) -> None:
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.win.geometry(f"+{x}+{y}")

    def _toggle_click_through(self, _event=None) -> None:
        self.set_click_through(not self.click_through)

    # ---- win32 click-through ----
    def set_click_through(self, enabled: bool) -> None:
        self.click_through = enabled
        if sys.platform != "win32":
            return
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000

            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            if not hwnd:
                hwnd = self.win.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enabled:
                style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            else:
                style &= ~WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    # ---- content ----
    def show(self) -> None:
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.win.lift()
        self.set_click_through(self.click_through)

    def hide(self) -> None:
        self.win.withdraw()

    def set_font_size(self, size: int) -> None:
        self.font_size = size
        self.label.configure(font=("Segoe UI", size, "bold"))
        self.original_label.configure(font=("Segoe UI", max(11, size - 7)))

    def set_max_lines(self, lines: int) -> None:
        self.max_lines = lines
        self._render()

    def set_fade_seconds(self, seconds: int) -> None:
        self.fade_seconds = seconds

    def clear(self) -> None:
        self._history.clear()
        self.label.configure(text="Subtitles will appear here…")
        self.original_label.configure(text="")

    def add(self, english: str, original: str = "") -> None:
        self._history.append((english, original))
        self._history = self._history[-self.max_lines:]
        self._render()
        try:
            self.win.attributes("-alpha", 0.86)
        except Exception:
            pass
        if self._fade_job is not None:
            try:
                self.root.after_cancel(self._fade_job)
            except Exception:
                pass
        if self.fade_seconds > 0:
            self._fade_job = self.root.after(
                int(self.fade_seconds * 1000), self._fade
            )

    def _render(self) -> None:
        self._history = self._history[-self.max_lines:]
        self.label.configure(text="\n".join(e for e, _ in self._history))
        last_original = self._history[-1][1] if self._history else ""
        self.original_label.configure(text=last_original or "")
        self.label.configure(wraplength=max(300, self.width - 60))
        self.original_label.configure(wraplength=max(300, self.width - 60))

    def _fade(self) -> None:
        try:
            self.win.attributes("-alpha", 0.25)
        except Exception:
            pass

    def geometry(self) -> str:
        try:
            return self.win.geometry()
        except Exception:
            return ""

    def destroy(self) -> None:
        if self._fade_job is not None:
            try:
                self.root.after_cancel(self._fade_job)
            except Exception:
                pass
        try:
            self.win.destroy()
        except Exception:
            pass
