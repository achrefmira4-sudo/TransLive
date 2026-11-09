"""Persistent settings for Voice Translator."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "VoiceTranslator"
APP_TITLE = "Voice Translator — Live to English"

DEFAULTS = {
    "model_size": "base",
    "device": "auto",
    "compute_type": "auto",
    "source_keyword": "",
    "language": "auto",
    "show_original": False,
    "skip_english": False,
    "speak_translation": False,
    "tts_rate": 175,
    "show_overlay": True,
    "overlay_click_through": False,
    "overlay_font_size": 22,
    "overlay_lines": 2,
    "overlay_fade_seconds": 8,
    "overlay_geometry": "",
    "response_speed": "balanced",
    "save_transcript": True,
    "close_to_tray": True,
    "window_geometry": "1000x820",
}


def app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_dir() -> Path:
    d = app_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcript_dir() -> Path:
    d = app_dir() / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Config:
    def __init__(self) -> None:
        self.path = app_dir() / "settings.json"
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self.data.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except Exception:
            pass

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def update(self, **kwargs) -> None:
        self.data.update(kwargs)
