"""Language codes shared by the translator and the UI."""
from __future__ import annotations

# Common choices first — useful for Discord servers around North Africa / Europe.
PRIORITY = [
    "ar", "fr", "en", "es", "de", "it", "pt", "ru", "tr", "zh", "ja", "ko",
    "hi", "ur", "fa", "nl", "pl", "uk", "id", "vi", "th", "sv", "no", "da",
    "fi", "el", "he", "ro", "hu", "cs", "bn", "ta", "tl", "ms", "sw", "az",
    "kk", "uz", "bg", "sr", "hr", "sk", "sl", "lt", "lv", "et", "ca", "eu",
]

LANG_NAMES = {
    "ar": "Arabic", "fr": "French", "en": "English", "es": "Spanish",
    "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "tr": "Turkish", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "hi": "Hindi", "ur": "Urdu", "fa": "Persian", "nl": "Dutch",
    "pl": "Polish", "uk": "Ukrainian", "id": "Indonesian", "vi": "Vietnamese",
    "th": "Thai", "sv": "Swedish", "no": "Norwegian", "da": "Danish",
    "fi": "Finnish", "el": "Greek", "he": "Hebrew", "ro": "Romanian",
    "hu": "Hungarian", "cs": "Czech", "bn": "Bengali", "ta": "Tamil",
    "tl": "Tagalog", "ms": "Malay", "sw": "Swahili", "az": "Azerbaijani",
    "kk": "Kazakh", "uz": "Uzbek", "bg": "Bulgarian", "sr": "Serbian",
    "hr": "Croatian", "sk": "Slovak", "sl": "Slovenian", "lt": "Lithuanian",
    "lv": "Latvian", "et": "Estonian", "ca": "Catalan", "eu": "Basque",
    "am": "Amharic", "so": "Somali", "ha": "Hausa", "yo": "Yoruba",
    "ber": "Berber", "ku": "Kurdish", "ps": "Pashto", "ne": "Nepali",
    "si": "Sinhala", "km": "Khmer", "lo": "Lao", "my": "Burmese",
    "ka": "Georgian", "hy": "Armenian", "mn": "Mongolian", "af": "Afrikaans",
}


def name_of(code: str) -> str:
    if not code:
        return "Unknown"
    return LANG_NAMES.get(code, code.upper())


def ui_choices() -> list[str]:
    """Values for the language dropdown."""
    out = ["Auto-detect (any language)"]
    for code in PRIORITY:
        if code in LANG_NAMES:
            out.append(f"{LANG_NAMES[code]} ({code})")
    return out


def code_from_choice(choice: str) -> str | None:
    """Return a language code, or None for auto-detect."""
    if not choice or choice.startswith("Auto"):
        return None
    if "(" in choice and choice.endswith(")"):
        return choice.rsplit("(", 1)[1][:-1].strip()
    return None


def choice_from_code(code: str | None) -> str:
    if not code or code == "auto":
        return "Auto-detect (any language)"
    if code in LANG_NAMES:
        return f"{LANG_NAMES[code]} ({code})"
    return "Auto-detect (any language)"
