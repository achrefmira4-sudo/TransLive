"""Headless self-test that also works inside a frozen (PyInstaller) build.

    VoiceTranslator.exe --selftest

This exists because a packaged app can fail in ways source never does: native
DLLs or data assets get left out of the bundle. The most fragile piece is the
Silero VAD model (an .onnx file) plus onnxruntime — the app runs Whisper with
vad_filter=True, so if those are missing the exe dies the moment someone speaks.

Results are printed AND written to a log file, because a windowed Windows .exe
has no console: %APPDATA%\\VoiceTranslator\\selftest.log
"""
from __future__ import annotations

import os
import sys

OK = "[ OK ]"
BAD = "[FAIL]"
INFO = "[INFO]"

_lines: list[str] = []


def say(msg: str = "") -> None:
    _lines.append(msg)
    try:
        print(msg)
    except Exception:
        # Windowed builds may have sys.stdout = None.
        pass


def _bundle_root() -> str:
    """Directory holding bundled assets (PyInstaller sets _MEIPASS)."""
    return getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _search_roots() -> list[str]:
    """Where the VAD asset can legitimately be found.

    Frozen build : inside the PyInstaller bundle (_MEIPASS).
    From source  : inside the installed faster_whisper package in site-packages.

    Searching only the project folder is wrong from source, and appears to work
    only while a dist/ build happens to sit inside the project — a false pass
    that disappears as soon as the build output is cleaned.
    """
    roots = [_bundle_root()]
    try:
        import importlib.util

        spec = importlib.util.find_spec("faster_whisper")
        if spec and spec.submodule_search_locations:
            roots.extend(spec.submodule_search_locations)
    except Exception:
        pass
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _find_vad_asset() -> str | None:
    for root in _search_roots():
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if "silero_vad" in f and f.endswith(".onnx"):
                    return os.path.join(dirpath, f)
    return None


def _write_log() -> str | None:
    try:
        from app.config import app_dir

        path = os.path.join(str(app_dir()), "selftest.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines) + "\n")
        return path
    except Exception:
        return None


def run() -> int:
    _lines.clear()
    frozen = bool(getattr(sys, "frozen", False))
    problems: list[str] = []

    say("=" * 64)
    say(" Voice Translator - self test")
    say("=" * 64)
    say(f"{INFO} Running as: {'PACKAGED EXE' if frozen else 'source'}")
    say(f"{INFO} Python {sys.version.split()[0]}")
    say(f"{INFO} Bundle root: {_bundle_root()}")

    say("\n--- App modules ---")
    for mod in ("app.audio", "app.vad", "app.translator", "app.pipeline",
                "app.overlay", "app.speech", "app.languages", "app.config"):
        try:
            __import__(mod)
            say(f"{OK} {mod}")
        except Exception as exc:
            say(f"{BAD} {mod}: {exc}")
            problems.append(f"cannot import {mod}")

    say("\n--- Speech libraries ---")
    for mod in ("numpy", "ctranslate2", "faster_whisper", "onnxruntime", "av"):
        try:
            m = __import__(mod)
            say(f"{OK} {mod} {getattr(m, '__version__', '')}")
        except Exception as exc:
            say(f"{BAD} {mod}: {exc}")
            problems.append(f"cannot import {mod}")

    say("\n--- Bundled VAD model ---")
    vad_path = _find_vad_asset()
    if vad_path:
        say(f"{OK} found {os.path.basename(vad_path)} "
            f"({os.path.getsize(vad_path)/1e6:.1f} MB)")
    else:
        say(f"{BAD} Silero VAD .onnx asset is missing from the bundle")
        problems.append("VAD asset not bundled - rebuild with the fixed spec")

    say("\n--- VAD inference (the part that breaks in bad builds) ---")
    try:
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        sr = 16000
        rng = np.random.default_rng(0)
        t = np.arange(sr) / sr
        tone = (0.3 * np.sin(2 * np.pi * 180 * t)
                + 0.2 * rng.standard_normal(sr)).astype(np.float32)
        audio = np.concatenate([
            np.zeros(sr // 2, np.float32), tone, np.zeros(sr // 2, np.float32)
        ])
        # This low-level call needs a VadOptions instance. (WhisperModel.transcribe
        # accepts a plain dict and converts it, which is what the app passes.)
        stamps = get_speech_timestamps(audio, vad_options=VadOptions(threshold=0.3))
        # A synthetic tone is not speech, so 0 regions is the correct answer.
        # What matters is that onnxruntime loaded the model and ran at all.
        say(f"{OK} onnxruntime loaded the VAD model and ran inference "
            f"({len(stamps)} region(s) in a synthetic tone)")
    except Exception as exc:
        say(f"{BAD} VAD inference failed: {type(exc).__name__}: {exc}")
        problems.append(f"VAD inference failed: {exc}")

    say("\n--- Noise gating ---")
    try:
        import numpy as np
        from app.vad import Segmenter

        seg = Segmenter.from_speed("balanced")
        quiet = (np.random.default_rng(1).standard_normal(16000 * 3)
                 * 0.0004).astype(np.float32)
        out = seg.push(quiet)
        if not out:
            say(f"{OK} silence produced no utterances")
        else:
            say(f"{BAD} silence produced {len(out)} utterance(s)")
            problems.append("noise gating regressed")
    except Exception as exc:
        say(f"{BAD} {exc}")
        problems.append(str(exc))

    say("\n--- Platform audio ---")
    if sys.platform == "win32":
        try:
            from app.audio import list_devices

            devs = list_devices()
            loops = [d for d in devs if d.kind == "loopback"]
            if loops:
                say(f"{OK} {len(loops)} 'What you hear' source(s), "
                    f"{len(devs) - len(loops)} microphone(s)")
                for d in loops:
                    say(f"       {d.name}")
            else:
                say(f"{BAD} no loopback sources found")
                problems.append("no 'What you hear' audio source")
        except Exception as exc:
            say(f"{BAD} audio capture unavailable: {exc}")
            problems.append(f"audio capture: {exc}")
    else:
        say(f"{INFO} not Windows - WASAPI loopback unavailable here (expected)")

    say("\n" + "=" * 64)
    if problems:
        say(f" {len(problems)} problem(s):")
        for p in problems:
            say(f"   - {p}")
    else:
        say(" All checks passed. The speech pipeline is intact.")
    log = _write_log()
    if log:
        say(f" Log written to: {log}")
    say("=" * 64)
    return 1 if problems else 0
