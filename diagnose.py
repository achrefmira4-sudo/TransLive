"""One-shot setup check for Voice Translator.

Run this if the app will not start or captures no audio:

    python diagnose.py

It checks Python, dependencies, audio devices, GPU availability and model
loading, then prints exactly what to fix. Safe to run: it records nothing and
only loads the model (no translation).
"""
from __future__ import annotations

import os
import platform
import sys

OK = "[ OK ]"
BAD = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"

problems: list[str] = []


def line(msg: str = "") -> None:
    print(msg, flush=True)


def check_python() -> None:
    line("=" * 68)
    line(" Voice Translator — setup diagnostic")
    line("=" * 68)
    v = sys.version_info
    line(f"{INFO} Python {v.major}.{v.minor}.{v.micro} ({platform.architecture()[0]})")
    line(f"{INFO} Platform: {platform.system()} {platform.release()}")

    if v < (3, 10):
        line(f"{BAD} Python 3.10 or newer is required.")
        problems.append("Install Python 3.10+ from https://www.python.org/downloads/")
    else:
        line(f"{OK} Python version is supported")

    if platform.architecture()[0] != "64bit":
        line(f"{BAD} 32-bit Python detected. The speech engine needs 64-bit.")
        problems.append("Install 64-bit Python (the 'Windows installer (64-bit)' download)")

    if platform.system() != "Windows":
        line(f"{WARN} Not Windows. System-audio capture only works on Windows;")
        line("       the app will open but list no audio sources here.")


def check_dependencies() -> None:
    line()
    line("--- Dependencies ---")
    mods = [
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("faster_whisper", "faster-whisper"),
        ("ctranslate2", "ctranslate2"),
        ("pyaudiowpatch", "PyAudioWPatch"),
        ("pyttsx3", "pyttsx3"),
    ]
    for mod, pip_name in mods:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "installed")
            line(f"{OK} {pip_name} ({ver})")
        except Exception as exc:
            if mod == "pyaudiowpatch" and platform.system() != "Windows":
                line(f"{WARN} {pip_name} unavailable (Windows-only) — expected here")
                continue
            if mod == "pyttsx3":
                line(f"{WARN} {pip_name} missing — spoken output disabled, subtitles still work")
                continue
            line(f"{BAD} {pip_name} missing or broken: {exc}")
            problems.append(f"Run: pip install -r requirements.txt  (missing {pip_name})")


def check_audio() -> None:
    line()
    line("--- Audio devices ---")
    if platform.system() != "Windows":
        line(f"{WARN} Skipped: WASAPI loopback requires Windows.")
        return
    try:
        from app.audio import HAS_PYAUDIO, list_devices

        if not HAS_PYAUDIO:
            line(f"{BAD} PyAudioWPatch not importable.")
            problems.append("Run: pip install PyAudioWPatch")
            return

        devices = list_devices()
        if not devices:
            line(f"{BAD} No audio devices found.")
            problems.append("Check that Windows has an enabled playback device "
                            "(right-click the speaker icon -> Sound settings)")
            return

        loopbacks = [d for d in devices if d.kind == "loopback"]
        mics = [d for d in devices if d.kind == "mic"]
        line(f"{OK} Found {len(loopbacks)} 'What you hear' source(s) "
             f"and {len(mics)} microphone(s)")
        for d in devices:
            star = " <- default" if d.is_default else ""
            line(f"       [{d.kind:8}] {d.name}  "
                 f"({d.rate} Hz, {d.channels} ch){star}")
        if not loopbacks:
            line(f"{WARN} No loopback sources. You can still use a microphone.")
        else:
            line(f"{INFO} Use a 'What you hear' source for Discord / calls / games.")
    except Exception as exc:
        line(f"{BAD} Could not enumerate devices: {exc}")
        problems.append(f"Audio enumeration failed: {exc}")


def check_gpu() -> str:
    line()
    line("--- Compute device ---")
    try:
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
        if n > 0:
            line(f"{OK} {n} CUDA GPU(s) detected — you can use small/medium models.")
            line(f"{INFO} Set Settings -> Processing = cuda")
            return "cuda"
        line(f"{INFO} No CUDA GPU detected — using CPU.")
        line(f"{INFO} On CPU use model 'base' (or 'tiny' on a slow PC).")
        return "cpu"
    except Exception as exc:
        line(f"{WARN} GPU check failed ({exc}); assuming CPU.")
        return "cpu"


def check_model(device: str) -> None:
    line()
    line("--- Model load test ---")
    try:
        from app.config import model_dir
        from app.translator import HAS_WHISPER, Translator

        if not HAS_WHISPER:
            line(f"{BAD} faster-whisper unavailable; skipping.")
            return

        line(f"{INFO} Models are stored in: {model_dir()}")
        line(f"{INFO} Loading 'tiny' to verify the engine (downloads ~75 MB once)...")
        tr = Translator(model_size="tiny", device=device,
                        download_root=str(model_dir()),
                        on_status=lambda m: line(f"       {m}"))
        tr.load()
        line(f"{OK} Model loaded successfully on {tr.actual_device.upper()}")

        import numpy as np

        # Half a second of quiet noise: must be rejected, not hallucinated.
        rng = np.random.default_rng(0)
        silence = (rng.standard_normal(8000) * 0.0004).astype(np.float32)
        result = tr.process(silence)
        if result is None:
            line(f"{OK} Silence correctly produced no text (noise gating works)")
        else:
            line(f"{WARN} Silence produced text: {result.english!r}")
    except Exception as exc:
        line(f"{BAD} Model load failed: {exc}")
        problems.append(f"Model could not load: {exc}. Check internet access for "
                        "the first download, and available disk space.")


def check_gui() -> None:
    line()
    line("--- Interface ---")
    try:
        import tkinter

        line(f"{OK} tkinter is available (Tk {tkinter.TkVersion})")
    except Exception as exc:
        line(f"{BAD} tkinter missing: {exc}")
        problems.append("Reinstall Python and ensure 'tcl/tk and IDLE' is selected")


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    check_python()
    check_dependencies()
    check_audio()
    device = check_gpu()
    check_gui()
    check_model(device)

    line()
    line("=" * 68)
    if not problems:
        line(" All checks passed. Start the app with run.bat (or python main.py).")
        line("=" * 68)
        return 0

    line(f" {len(problems)} thing(s) to fix:")
    for i, p in enumerate(problems, 1):
        line(f"   {i}. {p}")
    line("=" * 68)
    return 1


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 130
    if platform.system() == "Windows":
        try:
            input("\nPress Enter to close...")
        except Exception:
            pass
    raise SystemExit(code)
