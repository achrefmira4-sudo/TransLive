# PyInstaller spec — builds a one-folder Windows app.
#
#   pyinstaller --noconfirm VoiceTranslator.spec
#
# Or just double-click build_exe.bat.
#
# Notes:
#  * One-folder (not one-file) on purpose: a single .exe would unpack ~500 MB to
#    a temp dir on every launch, which is slow and trips antivirus.
#  * onnxruntime + faster_whisper's assets/silero_vad_v6.onnx MUST be bundled.
#    The app runs Whisper with vad_filter=True, so without them the packaged exe
#    crashes the moment somebody speaks, even though it works fine from source.
#  * The speech model itself is NOT bundled; it downloads on first Start so users
#    can change model size without rebuilding.

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Resolve paths relative to THIS SPEC FILE, not the shell's working directory.
# PyInstaller injects SPECPATH; using os.getcwd() silently loses the icon and
# app files when someone runs `pyinstaller path/to/VoiceTranslator.spec` from
# another folder.
try:
    HERE = os.path.abspath(SPECPATH)  # noqa: F821 - injected by PyInstaller
except NameError:  # pragma: no cover - direct exec outside PyInstaller
    HERE = os.path.dirname(os.path.abspath(__file__))

datas = []
binaries = []
hiddenimports = [
    # Windows audio capture (WASAPI loopback)
    "pyaudiowpatch",
    "_pyaudiowpatch",
    # Windows text-to-speech
    "pyttsx3",
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "pyttsx3.drivers.dummy",
    "comtypes",
    "comtypes.client",
    "comtypes.stream",
    "comtypes.gen",
    # pyttsx3's sapi5 driver imports pythoncom directly (verified in
    # pyttsx3/drivers/sapi5.py). pywin32 ships these as extension modules that
    # PyInstaller's static analysis misses, so TTS would ImportError in the exe.
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
    "win32api",
    "win32timezone",
    # resampling
    "scipy.signal",
    "scipy._lib.messagestream",
    # speech stack loaded lazily / by string
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "huggingface_hub",
    "tokenizers",
    "av",
    "av.audio.resampler",
]

# collect_all grabs data files + native DLLs, not just .py modules.
# onnxruntime and faster_whisper are the critical ones (VAD model asset).
for pkg in (
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "tokenizers",
    "av",
    "pyttsx3",
    "huggingface_hub",
    # comtypes generates its SpeechLib wrapper at runtime, so the package must
    # be present in full (and writable) rather than statically traced.
    "comtypes",
    "win32com",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # pragma: no cover
        print(f"[spec] WARNING: collect_all({pkg}) failed: {exc}")

try:
    hiddenimports += collect_submodules("scipy.signal")
    hiddenimports += collect_submodules("onnxruntime")
    hiddenimports += collect_submodules("pyttsx3")
except Exception as exc:  # pragma: no cover
    print(f"[spec] WARNING: collect_submodules failed: {exc}")

# Ship our own package explicitly so a frozen import can never miss it.
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    for f in files:
        if f.endswith(".py"):
            rel = os.path.relpath(os.path.join(root, f), HERE)
            datas.append((os.path.join(HERE, rel), os.path.dirname(rel) or "app"))

icon_path = os.path.join(HERE, "icon.ico")
icon = icon_path if os.path.exists(icon_path) else None

version_file = os.path.join(HERE, "version_info.txt")
version = version_file if (os.path.exists(version_file) and sys.platform == "win32") else None

a = Analysis(
    [os.path.join(HERE, "main.py")],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "IPython", "jupyter", "notebook", "pandas",
        "torch", "transformers", "tensorflow",
        "pytest", "setuptools._distutils",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX corrupts some native speech DLLs
    console=False,        # windowed app; set True temporarily to debug startup
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    version=version,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoiceTranslator",
)
