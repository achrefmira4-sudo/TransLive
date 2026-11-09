# Getting `VoiceTranslator.exe`

The `.exe` has to be built **on Windows**. PyInstaller cannot cross-compile, so a
Windows binary cannot be produced from Linux or macOS. Pick one of the two routes
below.

---

## Route A — build it on your PC (one double-click)

You need Python installed once, just for building. The finished app does **not**
need Python.

1. Install **Python 3.11 or 3.12 (64-bit)** from
   <https://www.python.org/downloads/> — tick **"Add python.exe to PATH"**.
2. Double-click **`build_exe.bat`**.
3. Wait. First build downloads dependencies and can take **5–15 minutes**.

Result:

```
dist\VoiceTranslator\VoiceTranslator.exe
```

Double-click that to run. To share the app, zip and send the **entire
`dist\VoiceTranslator` folder** — the `.exe` needs the files next to it.

The script checks for 64-bit Python, creates an isolated environment, installs
everything, builds, and tells you if anything failed. If it errors, run
`diagnose.bat` and read the last lines of the build output.

---

## Route B — let GitHub build it (no Python on your PC)

This repository includes a workflow that builds the `.exe` on a real Windows
machine and gives you a download.

1. Create a free account at <https://github.com> and make a **new repository**.
2. Upload the contents of this folder (drag the files into the repo page, or use
   GitHub Desktop). Make sure the `.github` folder is included.
3. Open the **Actions** tab.
4. Select **"Build Windows EXE"** → **Run workflow** → **Run workflow**.
5. Wait about 10 minutes, then open the finished run and download the
   **VoiceTranslator-Windows** artifact.
6. Unzip it and run `VoiceTranslator.exe`.

Pushing a tag like `v1.0.0` also builds it and attaches the zip to a GitHub
Release automatically.

---

## What to expect

| | |
| --- | --- |
| Build time | 5–15 min the first time |
| App folder size | roughly **400–700 MB** |
| Startup | a few seconds |
| Speech model | downloaded separately on first **Start** (75 MB–3 GB by size) |
| Python needed to run the built app | **No** |

The size is normal: it bundles the numeric and speech-inference libraries
(`ctranslate2`, `numpy`, `scipy`, `onnxruntime`, `av`). The model is deliberately
*not* bundled so you can switch model sizes without rebuilding.

---

## One-folder vs one-file

The build produces a **one-folder** app on purpose. A single-file `.exe` would
have to unpack ~500 MB to a temp directory on every launch, making startup slow
and often triggering antivirus warnings. One-folder starts faster and is more
reliable. Zip the folder if you want a single file to send.

---

## Antivirus note

Freshly built PyInstaller apps are sometimes flagged by Windows SmartScreen or
antivirus because they are unsigned. This is a false positive. You can run it via
**More info → Run anyway**, or add an exclusion for the folder. Code-signing
certificates remove the warning but cost money and are not included here.

---

## Verify the built app

Double-click **`selftest.bat`** (it sits next to the `.exe` in the output folder).
It runs the packaged app headlessly and checks that packaging preserved
everything:

- all app modules and native speech libraries import
- the Silero VAD `.onnx` model is inside the bundle
- **VAD inference actually runs** — this is the check that matters, because a
  missing `onnxruntime` or VAD asset produces an app that opens fine and then
  crashes the first time somebody speaks
- silence is correctly ignored
- your `[What you hear]` audio sources are listed

Because the app is windowed and has no console, results are also saved to
`%APPDATA%\VoiceTranslator\selftest.log`, and `selftest.bat` prints that log for
you. You can also run it directly:

```
VoiceTranslator.exe --selftest
```

The GitHub build runs this same self-test on the `.exe` before uploading it, so a
broken bundle fails the build instead of reaching you.

---

## Build troubleshooting

**"Python is 32-bit"** — install the 64-bit Python installer; the speech engine
requires 64-bit.

**Build fails on `pyaudiowpatch`** — use Python 3.10–3.13. Prebuilt wheels exist
for those versions; other versions try to compile from source and need Visual
Studio build tools.

**`dist` folder is missing after building** — the build failed earlier. Scroll up
in the console for the first error, or check `build\VoiceTranslator\warn-VoiceTranslator.txt`.

**The exe opens and closes instantly** — run `selftest.bat` first; it reports
missing modules and assets. You can also run `VoiceTranslator.exe --selftest`, or
temporarily set `console=True` in `VoiceTranslator.spec` and rebuild to get a
visible console window.

**The exe opens but crashes when someone speaks** — almost always the VAD model
or `onnxruntime` missing from the bundle. `selftest.bat` confirms this in one
step. Make sure you are building with the included `VoiceTranslator.spec`, which
collects both.

**Missing module at runtime** — add it to `hiddenimports` in
`VoiceTranslator.spec` and rebuild.
