"""Voice Translator - live speech translation to English for Windows.

Captures whatever you hear (Discord voice channels, Instagram/WhatsApp browser
calls, Zoom, games, videos) or your microphone, and shows English subtitles in
real time. Optionally speaks the English translation out loud.

Run:  python main.py
"""
from __future__ import annotations

import datetime as _dt
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.audio import HAS_PYAUDIO, Device, list_devices
from app.config import APP_TITLE, Config, transcript_dir
from app.overlay import SubtitleOverlay
from app.pipeline import Event, Pipeline
from app.speech import Speaker
from app.translator import HAS_WHISPER, MODEL_NOTES, MODEL_SIZES, Translator
from app.languages import (
    choice_from_code, code_from_choice, name_of, ui_choices,
)

BG = "#15161A"
PANEL = "#1E2027"
FG = "#E8EAED"
MUTED = "#9AA0A6"
ACCENT = "#4C8DFF"
GREEN = "#3DDC84"
RED = "#FF5C5C"
AMBER = "#FFB454"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = Config()
        self.events: "queue.Queue[Event]" = queue.Queue(maxsize=512)
        self.devices: list[Device] = []
        self.pipeline: Pipeline | None = None
        self.translator: Translator | None = None
        self.speaker: Speaker | None = None
        self.transcript_lines: list[str] = []
        self.transcript_file = None
        self._level_job = None
        # Shutdown runs on a worker thread; these prevent a fast Stop->Start
        # from orphaning a live pipeline (see stop()/_stop_worker).
        self._stopping = False
        self._session_id = 0
        self._stop_thread: threading.Thread | None = None

        root.title(APP_TITLE)
        root.geometry(self.cfg.get("window_geometry"))
        root.minsize(820, 600)
        root.configure(bg=BG)

        self._build_styles()
        self._build_ui()

        self.overlay = SubtitleOverlay(
            root,
            font_size=self.cfg.get("overlay_font_size"),
            lines=self.cfg.get("overlay_lines"),
            fade_seconds=self.cfg.get("overlay_fade_seconds"),
            click_through=self.cfg.get("overlay_click_through"),
            geometry=self.cfg.get("overlay_geometry"),
        )

        self.refresh_devices()
        self._check_environment()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._drain_events)

    # ---------------- UI ----------------
    def _build_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG,
                        font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background=PANEL, foreground=FG,
                        font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=BG, foreground=FG,
                        font=("Segoe UI Semibold", 17))
        style.configure("TCheckbutton", background=BG, foreground=FG,
                        font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=FG, arrowcolor=FG, padding=5)
        style.configure("TSpinbox", fieldbackground=PANEL, background=PANEL,
                        foreground=FG, arrowcolor=FG)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                        padding=(16, 8), font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", ACCENT)],
                  foreground=[("selected", "#FFFFFF")])
        style.configure("Horizontal.TProgressbar", background=GREEN,
                        troughcolor=PANEL, borderwidth=0)

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=18, pady=(16, 8))
        ttk.Label(header, text="🌐  Voice Translator", style="Header.TLabel").pack(side="left")
        self.status_dot = tk.Canvas(header, width=12, height=12, bg=BG,
                                    highlightthickness=0)
        self.status_dot.pack(side="right", padx=(8, 0))
        self._dot = self.status_dot.create_oval(2, 2, 11, 11, fill=MUTED, outline="")
        self.status_label = ttk.Label(header, text="Idle", style="Muted.TLabel")
        self.status_label.pack(side="right")

        # IMPORTANT: pack the action bar BEFORE the notebook, anchored to the
        # bottom. Tk's packer allocates space in packing order, so a notebook
        # packed first with expand=True consumes the whole window and starves
        # anything packed after it — the Start button ended up unmapped with
        # height=1 whenever the content requested more height than the window
        # had. Reserving the bar first guarantees it is always visible.
        bar = ttk.Frame(self.root)
        bar.pack(side="bottom", fill="x", padx=18, pady=(0, 14))

        tabs = ttk.Notebook(self.root)
        tabs.pack(side="top", fill="both", expand=True, padx=18, pady=(4, 12))
        self.notebook = tabs

        live = ttk.Frame(tabs)
        settings = ttk.Frame(tabs)
        help_tab = ttk.Frame(tabs)
        tabs.add(live, text="  Live  ")
        tabs.add(settings, text="  Settings  ")
        tabs.add(help_tab, text="  How to use  ")

        self._build_live_tab(live)
        self._build_settings_tab(settings)
        self._build_help_tab(help_tab)
        self.start_btn = tk.Button(
            bar, text="▶  Start translating", command=self.toggle,
            bg=ACCENT, fg="white", activebackground="#3A78E0",
            activeforeground="white", font=("Segoe UI Semibold", 12),
            relief="flat", padx=22, pady=10, cursor="hand2", bd=0,
        )
        self.start_btn.pack(side="left")
        tk.Button(
            bar, text="Clear", command=self.clear_transcript,
            bg=PANEL, fg=FG, activebackground="#2A2D36", activeforeground=FG,
            font=("Segoe UI", 10), relief="flat", padx=16, pady=10,
            cursor="hand2", bd=0,
        ).pack(side="left", padx=8)
        tk.Button(
            bar, text="Save transcript", command=self.save_transcript,
            bg=PANEL, fg=FG, activebackground="#2A2D36", activeforeground=FG,
            font=("Segoe UI", 10), relief="flat", padx=16, pady=10,
            cursor="hand2", bd=0,
        ).pack(side="left")
        self.level = ttk.Progressbar(bar, maximum=100, length=160,
                                     style="Horizontal.TProgressbar")
        self.level.pack(side="right", pady=10)
        ttk.Label(bar, text="Input level", style="Muted.TLabel").pack(
            side="right", padx=(0, 8))

    def _build_live_tab(self, parent) -> None:
        top = ttk.Frame(parent)
        top.pack(fill="x", pady=(14, 8), padx=4)
        ttk.Label(top, text="Audio source:").pack(side="left")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(top, textvariable=self.device_var,
                                         state="readonly", width=62)
        self.device_combo.pack(side="left", padx=10)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)
        tk.Button(top, text="⟳", command=self.refresh_devices, bg=PANEL, fg=FG,
                  activebackground="#2A2D36", activeforeground=FG,
                  relief="flat", padx=10, cursor="hand2", bd=0).pack(side="left")

        self.source_hint = ttk.Label(
            parent,
            text='For Discord, Instagram, Zoom or game chat pick a '
                 '[What you hear] source. A [Microphone] only hears you and '
                 'the room.',
            style="Muted.TLabel", wraplength=820, justify="left",
        )
        self.source_hint.pack(fill="x", padx=4, pady=(0, 10))

        box = tk.Frame(parent, bg=PANEL, bd=0)
        box.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        self.transcript = tk.Text(
            box, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
            font=("Segoe UI", 12), wrap="word", padx=16, pady=14,
            spacing1=3, spacing3=10, state="disabled", height=14,
        )
        scroll = ttk.Scrollbar(box, command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.transcript.pack(side="left", fill="both", expand=True)
        self.transcript.tag_configure("meta", foreground=MUTED,
                                      font=("Segoe UI", 9))
        self.transcript.tag_configure("original", foreground="#8AB4F8",
                                      font=("Segoe UI", 11))
        self.transcript.tag_configure("english", foreground=FG,
                                      font=("Segoe UI", 13, "bold"))
        self.transcript.tag_configure("system", foreground=MUTED,
                                      font=("Segoe UI", 10, "italic"))

    def _build_settings_tab(self, parent) -> None:
        # Settings is taller than the notebook area on small windows / large
        # display scaling, so put it in a scrollable canvas. Without this the
        # bottom rows were clipped and unreachable.
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        wrap = ttk.Frame(canvas)
        wrap_id = canvas.create_window((0, 0), window=wrap, anchor="nw")

        def _sync_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event):
            canvas.itemconfigure(wrap_id, width=event.width)

        wrap.bind("<Configure>", _sync_region)
        canvas.bind("<Configure>", _sync_width)

        def _wheel(event):
            # bind_all is needed because the pointer is usually over a child
            # widget, but it must not steal scrolling from the other tabs.
            try:
                if self.notebook.select() != str(parent):
                    return
                # Only scroll when the pointer is actually over this canvas.
                x, y = canvas.winfo_pointerxy()
                cx, cy = canvas.winfo_rootx(), canvas.winfo_rooty()
                if not (cx <= x < cx + canvas.winfo_width()
                        and cy <= y < cy + canvas.winfo_height()):
                    return
                if canvas.winfo_height() >= wrap.winfo_reqheight():
                    return  # nothing to scroll
            except Exception:
                return
            if event.delta:
                canvas.yview_scroll(int(-event.delta / 120), "units")
            else:  # Linux button 4/5
                canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
            return "break"

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, _wheel, add="+")

        self.settings_canvas = canvas
        self._settings_body(wrap)

    def _settings_body(self, wrap) -> None:
        wrap.configure(padding=(6, 14))

        r = 0
        ttk.Label(wrap, text="Model size:").grid(row=r, column=0, sticky="w", pady=6)
        self.model_var = tk.StringVar(value=self.cfg.get("model_size"))
        cb = ttk.Combobox(wrap, textvariable=self.model_var, values=MODEL_SIZES,
                          state="readonly", width=16)
        cb.grid(row=r, column=1, sticky="w", padx=10)
        cb.bind("<<ComboboxSelected>>", lambda e: self._model_note())
        self.model_note = ttk.Label(wrap, text="", style="Muted.TLabel")
        self.model_note.grid(row=r, column=2, sticky="w")
        self._model_note()

        r += 1
        ttk.Label(wrap, text="Processing:").grid(row=r, column=0, sticky="w", pady=6)
        self.device_mode_var = tk.StringVar(value=self.cfg.get("device"))
        ttk.Combobox(wrap, textvariable=self.device_mode_var,
                     values=["auto", "cpu", "cuda"], state="readonly",
                     width=16).grid(row=r, column=1, sticky="w", padx=10)
        ttk.Label(wrap, text="auto = use NVIDIA GPU if available",
                  style="Muted.TLabel").grid(row=r, column=2, sticky="w")

        r += 1
        ttk.Label(wrap, text="Spoken language:").grid(row=r, column=0, sticky="w", pady=6)
        self.language_var = tk.StringVar(
            value=choice_from_code(self.cfg.get("language")))
        ttk.Combobox(wrap, textvariable=self.language_var, values=ui_choices(),
                     state="readonly", width=26).grid(
            row=r, column=1, sticky="w", padx=10)
        ttk.Label(wrap, text="Lock this if you know the language — big accuracy win",
                  style="Muted.TLabel").grid(row=r, column=2, sticky="w")

        r += 1
        ttk.Label(wrap, text="Response speed:").grid(row=r, column=0, sticky="w", pady=6)
        self.speed_var = tk.StringVar(value=self.cfg.get("response_speed"))
        ttk.Combobox(wrap, textvariable=self.speed_var,
                     values=["fast", "balanced", "accurate"], state="readonly",
                     width=16).grid(row=r, column=1, sticky="w", padx=10)
        ttk.Label(wrap, text="fast = shorter pauses, accurate = fuller sentences",
                  style="Muted.TLabel").grid(row=r, column=2, sticky="w")

        r += 1
        ttk.Separator(wrap).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)

        self.show_original_var = tk.BooleanVar(value=self.cfg.get("show_original"))
        self.skip_english_var = tk.BooleanVar(value=self.cfg.get("skip_english"))
        self.speak_var = tk.BooleanVar(value=self.cfg.get("speak_translation"))
        self.overlay_var = tk.BooleanVar(value=self.cfg.get("show_overlay"))
        self.click_var = tk.BooleanVar(value=self.cfg.get("overlay_click_through"))
        self.save_var = tk.BooleanVar(value=self.cfg.get("save_transcript"))

        checks = [
            ("Also show the original language text (doubles CPU cost)",
             self.show_original_var),
            ("Ignore speech that is already English", self.skip_english_var),
            ("Speak the English translation out loud", self.speak_var),
            ("Show floating subtitle overlay", self.overlay_var),
            ("Overlay is click-through (double-click it to toggle)", self.click_var),
            ("Auto-save transcripts to a file", self.save_var),
        ]
        for text, var in checks:
            r += 1
            ttk.Checkbutton(wrap, text=text, variable=var).grid(
                row=r, column=0, columnspan=3, sticky="w", pady=4)

        r += 1
        ttk.Separator(wrap).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)

        r += 1
        ttk.Label(wrap, text="Overlay font size:").grid(row=r, column=0, sticky="w", pady=6)
        self.font_var = tk.IntVar(value=self.cfg.get("overlay_font_size"))
        ttk.Spinbox(wrap, from_=12, to=48, textvariable=self.font_var,
                    width=8, command=self._apply_overlay).grid(
            row=r, column=1, sticky="w", padx=10)

        r += 1
        ttk.Label(wrap, text="Overlay lines:").grid(row=r, column=0, sticky="w", pady=6)
        self.lines_var = tk.IntVar(value=self.cfg.get("overlay_lines"))
        ttk.Spinbox(wrap, from_=1, to=5, textvariable=self.lines_var,
                    width=8, command=self._apply_overlay).grid(
            row=r, column=1, sticky="w", padx=10)

        r += 1
        ttk.Label(wrap, text="Fade after (seconds, 0 = never):").grid(
            row=r, column=0, sticky="w", pady=6)
        self.fade_var = tk.IntVar(value=self.cfg.get("overlay_fade_seconds"))
        ttk.Spinbox(wrap, from_=0, to=60, textvariable=self.fade_var,
                    width=8, command=self._apply_overlay).grid(
            row=r, column=1, sticky="w", padx=10)

        r += 1
        ttk.Label(wrap, text="Speech rate:").grid(row=r, column=0, sticky="w", pady=6)
        self.rate_var = tk.IntVar(value=self.cfg.get("tts_rate"))
        ttk.Spinbox(wrap, from_=100, to=260, textvariable=self.rate_var,
                    width=8, command=self._apply_rate).grid(
            row=r, column=1, sticky="w", padx=10)

        r += 1
        ttk.Label(wrap, text="Settings apply the next time you press Start.",
                  style="Muted.TLabel").grid(row=r, column=0, columnspan=3,
                                             sticky="w", pady=(14, 0))
        wrap.columnconfigure(2, weight=1)

    def _build_help_tab(self, parent) -> None:
        text = tk.Text(parent, bg=BG, fg=FG, relief="flat", wrap="word",
                       font=("Segoe UI", 11), padx=18, pady=16, spacing3=6)
        text.pack(fill="both", expand=True)
        text.tag_configure("h", font=("Segoe UI Semibold", 13), foreground=ACCENT,
                           spacing1=10, spacing3=6)
        content = [
            ("h", "Translating Discord, Instagram, WhatsApp, Zoom and games"),
            ("", "1. In Settings choose a model — start with \"base\". Then set "
                 "\"Spoken language\" to the language people speak (Arabic, French, "
                 "etc.) for the best accuracy, or leave it on Auto-detect for "
                 "mixed-language calls."),
            ("", "2. On the Live tab pick an audio source that starts with "
                 "[What you hear]. That captures everything playing on your PC, "
                 "including Discord voice channels and calls in your browser."),
            ("", "3. Press Start translating. The first start downloads the model "
                 "(one time only, 75 MB to 3 GB depending on size)."),
            ("", "4. English subtitles appear in the window and in the floating "
                 "overlay. Drag the overlay anywhere; double-click it to make it "
                 "click-through so it never blocks your game or app."),
            ("h", "Important: use headphones for spoken translation"),
            ("", "If you enable \"Speak the English translation out loud\" while "
                 "capturing [What you hear], use headphones. Otherwise the app "
                 "hears its own voice. The app pauses listening while it speaks, "
                 "but headphones make it reliable."),
            ("h", "Translating your own microphone"),
            ("", "Pick a [Microphone] source to translate someone speaking in the "
                 "room. To send translated English into Discord as your voice, "
                 "install VB-Audio Virtual Cable, enable spoken translation, set "
                 "Windows default output to CABLE Input, and set Discord's input "
                 "device to CABLE Output."),
            ("h", "Tips for better accuracy"),
            ("", "• BIGGEST WIN: set \"Spoken language\" in Settings to the language "
                 "people actually speak (e.g. Arabic or French) instead of "
                 "Auto-detect. In testing, short auto-detected clips were sometimes "
                 "mistaken for the wrong language; locking it removes that error "
                 "entirely and is slightly faster."),
            ("", "• Leave Auto-detect on for mixed-language servers where people "
                 "switch between languages mid-call."),
            ("", "• Turn off noise suppression / echo cancellation in Discord if "
                 "words get cut off, and raise the caller's volume."),
            ("", "• Use \"accurate\" response speed for long sentences, \"fast\" for "
                 "quick back-and-forth chat."),
            ("", "• Model choice matters more than anything for speed. On a typical "
                 "CPU, \"base\" keeps up with live speech; \"small\" measured about "
                 "3x slower than realtime and will fall behind, so use it only with "
                 "an NVIDIA GPU (Processing = cuda). Start with \"base\"."),
            ("", "• If you see \"Skipped phrases to stay live\", the model is too "
                 "slow for this PC — pick a smaller one."),
            ("", "• \"Also show original text\" runs the model twice per phrase and "
                 "doubles the CPU cost. Leave it off if you fall behind."),
            ("", "• Everything runs locally. No audio leaves your computer and no "
                 "API key is needed."),
            ("h", "Troubleshooting"),
            ("", "No audio sources listed: run the app on Windows and install "
                 "dependencies with pip install -r requirements.txt."),
            ("", "Nothing gets translated: check the input level bar moves while "
                 "people talk. If it stays at zero you picked the wrong output "
                 "device — try another [What you hear] entry."),
            ("", "Slow or laggy: use a smaller model, or set Processing to cuda "
                 "with an NVIDIA GPU."),
        ]
        for tag, line in content:
            text.insert("end", line + "\n", tag if tag else ())
        text.configure(state="disabled")

    # ---------------- helpers ----------------
    def _model_note(self) -> None:
        self.model_note.configure(text=MODEL_NOTES.get(self.model_var.get(), ""))

    def _apply_overlay(self) -> None:
        try:
            self.overlay.set_font_size(int(self.font_var.get()))
            self.overlay.set_max_lines(int(self.lines_var.get()))
            self.overlay.set_fade_seconds(int(self.fade_var.get()))
        except Exception:
            pass

    def _apply_rate(self) -> None:
        if self.speaker is not None:
            try:
                self.speaker.set_rate(int(self.rate_var.get()))
            except Exception:
                pass

    def _check_environment(self) -> None:
        problems = []
        if sys.platform != "win32":
            problems.append("This app is designed for Windows. Audio capture "
                            "(WASAPI loopback) only works on Windows.")
        if not HAS_PYAUDIO:
            problems.append("PyAudioWPatch is missing — run: "
                            "pip install -r requirements.txt")
        if not HAS_WHISPER:
            problems.append("faster-whisper is missing — run: "
                            "pip install -r requirements.txt")
        if problems:
            self._append_system("⚠ " + "\n⚠ ".join(problems))
            self.set_status("Setup needed", RED)

    def refresh_devices(self) -> None:
        try:
            self.devices = list_devices()
        except Exception as exc:
            self.devices = []
            self._append_system(f"Could not list audio devices: {exc}")
        labels = [d.label for d in self.devices]
        self.device_combo.configure(values=labels)
        if labels:
            wanted = self.cfg.get("source_keyword")
            chosen = 0
            if wanted:
                for i, d in enumerate(self.devices):
                    if wanted.lower() in d.name.lower():
                        chosen = i
                        break
            else:
                for i, d in enumerate(self.devices):
                    if d.kind == "loopback" and d.is_default:
                        chosen = i
                        break
            self.device_combo.current(chosen)
        else:
            self.device_var.set("No audio devices found")

    def explain_microphone(self) -> None:
        """Tell the user a mic cannot hear the far end of a call."""
        self._append_system(
            "ℹ This is a MICROPHONE — it hears you and the room, not the "
            "other people in a call.\nTo translate Discord, Instagram, "
            "Zoom or game chat, stop and choose a source that starts with "
            "[What you hear].")

    def _on_device_change(self, _event=None) -> None:
        i = self.device_combo.current()
        if 0 <= i < len(self.devices):
            dev = self.devices[i]
            self.cfg.set("source_keyword", dev.name)
            # Warn at selection time, before the user presses Start.
            if dev.kind == "mic":
                self.source_hint.configure(
                    text="⚠ A MICROPHONE only hears you and the room — it will "
                         "not capture the other people in a Discord or phone "
                         "call. Choose a [What you hear] source for that.",
                    foreground=AMBER)
            else:
                self.source_hint.configure(
                    text="✓ This captures everything playing on your PC — "
                         "Discord, calls, videos and games.",
                    foreground=ACCENT)

    def set_status(self, text: str, color: str = MUTED) -> None:
        self.status_label.configure(text=text)
        self.status_dot.itemconfigure(self._dot, fill=color)

    def _append_system(self, message: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", message + "\n", "system")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _append_translation(self, ev: Event) -> None:
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        lang = name_of(ev.language)
        meta = f"{stamp}   {lang} → English"
        if ev.probability:
            meta += f"   ({ev.probability * 100:.0f}% confidence)"

        self.transcript.configure(state="normal")
        self.transcript.insert("end", meta + "\n", "meta")
        if ev.original:
            self.transcript.insert("end", ev.original + "\n", "original")
        self.transcript.insert("end", ev.text + "\n", "english")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

        line = f"[{stamp}] {lang} -> EN: {ev.text}"
        if ev.original:
            line += f"   (original: {ev.original})"
        self.transcript_lines.append(line)
        if self.transcript_file is not None:
            try:
                self.transcript_file.write(line + "\n")
                self.transcript_file.flush()
            except Exception:
                pass

        if self.overlay_var.get():
            self.overlay.add(ev.text, ev.original if self.show_original_var.get() else "")

    def clear_transcript(self) -> None:
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self.transcript_lines.clear()
        self.overlay.clear()

    def save_transcript(self) -> None:
        if not self.transcript_lines:
            messagebox.showinfo("Nothing to save", "The transcript is empty.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"translation-{_dt.datetime.now():%Y%m%d-%H%M%S}.txt",
            filetypes=[("Text file", "*.txt")],
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(self.transcript_lines))
                self._append_system(f"Saved transcript to {path}")
            except Exception as exc:
                messagebox.showerror("Save failed", str(exc))

    # ---------------- start / stop ----------------
    def toggle(self) -> None:
        if self._stopping:
            return  # ignore clicks until the previous session is fully closed
        if self.pipeline is not None and self.pipeline.running:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self._stopping:
            self.set_status("Still stopping, one moment…", MUTED)
            return
        if self.pipeline is not None and self.pipeline.running:
            return
        idx = self.device_combo.current()
        if not (0 <= idx < len(self.devices)):
            messagebox.showerror(
                "No audio source",
                "No audio input device is selected.\n\n"
                "On Windows, install dependencies first:\n"
                "pip install -r requirements.txt",
            )
            return
        if not HAS_WHISPER:
            messagebox.showerror(
                "Missing dependency",
                "faster-whisper is not installed.\n\n"
                "Open Command Prompt in this folder and run:\n"
                "pip install -r requirements.txt",
            )
            return

        self._save_settings()
        device = self.devices[idx]

        self.translator = Translator(
            model_size=self.model_var.get(),
            device=self.device_mode_var.get(),
            download_root=str(self._model_dir()),
            on_status=lambda m: self.events.put(Event("status", text=m)),
            language=code_from_choice(self.language_var.get()),
        )

        self.speaker = Speaker(rate=int(self.rate_var.get()))
        self.speaker.enabled = bool(self.speak_var.get())
        if self.speaker.enabled:
            self.speaker.start()

        self.pipeline = Pipeline(
            self.translator, self.speaker, self.events,
            want_original=bool(self.show_original_var.get()),
            skip_english=bool(self.skip_english_var.get()),
            speed=self.speed_var.get(),
        )

        if self.save_var.get():
            try:
                path = transcript_dir() / f"session-{_dt.datetime.now():%Y%m%d-%H%M%S}.txt"
                self.transcript_file = open(path, "a", encoding="utf-8")
                self._append_system(f"Logging to {path}")
            except Exception:
                self.transcript_file = None

        try:
            self.pipeline.start(device)
        except Exception as exc:
            messagebox.showerror("Could not start audio", str(exc))
            self.pipeline = None
            return

        if self.overlay_var.get():
            self.overlay.clear()
            self.overlay.set_click_through(bool(self.click_var.get()))
            self.overlay.show()
        else:
            self.overlay.hide()

        self.start_btn.configure(text="■  Stop", bg=RED, activebackground="#E04848")
        self.set_status("Starting…", ACCENT)
        self._append_system(f"Started. Source: {device.label}")

        # A microphone hears the room (and you), NOT the people in a Discord
        # call. This is the single most common wrong setting, so say so plainly
        # instead of letting the user wonder why nothing is translated.
        if device.kind == "mic":
            self.explain_microphone()

    def _model_dir(self):
        from app.config import model_dir

        return model_dir()

    def stop(self) -> None:
        """Begin shutdown.

        Capture the pipeline/transcript/speaker we are closing and hand them to
        the worker by value. The worker must never touch whatever `self.*`
        happens to point at later, or a quick Stop->Start would let the old
        session tear down the new one (leaving an unstoppable audio stream).
        """
        if self._stopping:
            return
        self._session_id += 1
        session = self._session_id
        pipeline = self.pipeline
        speaker = self.speaker
        transcript = self.transcript_file

        self.pipeline = None
        self.speaker = None
        self.transcript_file = None

        if speaker is not None:
            speaker.enabled = False

        self.set_status("Stopping…", MUTED)
        self.level.configure(value=0)
        self.start_btn.configure(text="Stopping…", bg=MUTED,
                                 activebackground=MUTED, state="disabled")

        if pipeline is None and speaker is None and transcript is None:
            self._finish_stop(session)
            return

        self._stopping = True
        self._stop_thread = threading.Thread(
            target=self._stop_worker,
            args=(session, pipeline, speaker, transcript),
            daemon=True,
        )
        self._stop_thread.start()

    def _stop_worker(self, session, pipeline, speaker, transcript) -> None:
        try:
            if speaker is not None:
                speaker.stop()
        except Exception:
            pass
        try:
            if pipeline is not None:
                pipeline.stop()
        except Exception:
            pass
        if transcript is not None:
            try:
                transcript.close()
            except Exception:
                pass
        # Back to the Tk thread: widget updates must not happen off-thread.
        try:
            self.root.after(0, lambda: self._finish_stop(session))
        except Exception:
            self._stopping = False

    def _finish_stop(self, session) -> None:
        if session != self._session_id:
            return  # a newer session already took over
        self._stopping = False
        self.start_btn.configure(text="▶  Start translating", bg=ACCENT,
                                 activebackground="#3A78E0", state="normal")
        self.set_status("Idle", MUTED)

    # ---------------- event loop ----------------
    def _drain_events(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                if ev.kind == "translation":
                    self._append_translation(ev)
                    self.set_status("Listening", GREEN)
                elif ev.kind == "status":
                    self.set_status(ev.text[:60], GREEN if "ready" in ev.text.lower()
                                    else ACCENT)
                    self._append_system(ev.text)
                elif ev.kind == "error":
                    self._append_system("⚠ " + ev.text)
                    self.set_status("Error", RED)
                elif ev.kind == "perf":
                    if ev.text:
                        self._append_system("⚠ " + ev.text)
                        self.set_status("Model too slow — see Live tab", RED)
                    elif ev.dropped:
                        self._append_system(
                            f"⚠ Skipped {ev.dropped} phrase(s) so far to keep "
                            "subtitles in sync. If this keeps happening, see "
                            "Settings → model size.")
                elif ev.kind == "level":
                    self.level.configure(value=min(100, ev.level * 700))
                elif ev.kind == "stopped":
                    self.set_status("Idle", MUTED)
                    self.level.configure(value=0)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)

    # ---------------- shutdown ----------------
    def _save_settings(self) -> None:
        self.cfg.update(
            model_size=self.model_var.get(),
            device=self.device_mode_var.get(),
            response_speed=self.speed_var.get(),
            language=code_from_choice(self.language_var.get()) or "auto",
            show_original=bool(self.show_original_var.get()),
            skip_english=bool(self.skip_english_var.get()),
            speak_translation=bool(self.speak_var.get()),
            show_overlay=bool(self.overlay_var.get()),
            overlay_click_through=bool(self.click_var.get()),
            save_transcript=bool(self.save_var.get()),
            overlay_font_size=int(self.font_var.get()),
            overlay_lines=int(self.lines_var.get()),
            overlay_fade_seconds=int(self.fade_var.get()),
            tts_rate=int(self.rate_var.get()),
            overlay_geometry=self.overlay.geometry(),
            window_geometry=self.root.geometry(),
        )
        i = self.device_combo.current()
        if 0 <= i < len(self.devices):
            self.cfg.set("source_keyword", self.devices[i].name)
        self.cfg.save()

    def on_close(self) -> None:
        self._save_settings()
        try:
            if self.speaker is not None:
                self.speaker.enabled = False
                self.speaker.stop()
            if self.pipeline is not None:
                self.pipeline.stop()
        except Exception:
            pass
        # A stop may already be in flight; let it release the audio device
        # before the process exits so the stream is not left locked.
        t = self._stop_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=6.0)
            except Exception:
                pass
        if self.transcript_file is not None:
            try:
                self.transcript_file.close()
            except Exception:
                pass
        self.overlay.destroy()
        self.root.destroy()


def main() -> None:
    # Headless self-test, works in the packaged .exe too:
    #   VoiceTranslator.exe --selftest
    if "--selftest" in sys.argv:
        from app.selftest import run

        raise SystemExit(run())

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
