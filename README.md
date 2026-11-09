# Voice Translator — live speech to English for Windows

Translates **any spoken language into English in real time** from whatever is
playing on your PC: Discord voice channels, Instagram / WhatsApp / Messenger
calls in a browser, Zoom, Teams, in-game voice chat, videos, or a microphone in
the room.

It listens to your **system audio** (WASAPI loopback), so it works with any app
without plugins, bots, or integrations. Nothing is uploaded — speech recognition
and translation run entirely on your own computer, and no API key is needed.

---

## Quick start (2 minutes)

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>.
   During setup, tick **"Add python.exe to PATH"**.
2. Double-click **`run.bat`**.

The first run creates a private environment and installs dependencies. The first
time you press **Start**, the speech model downloads (one time only).

Then:

3. On the **Live** tab pick an audio source starting with **`[What you hear]`**.
4. Press **▶ Start translating**.

English subtitles appear in the window and in a floating overlay you can drag
anywhere on screen.

---

## Using it with Discord, Instagram, and calls

| What you want to translate | What to select |
| --- | --- |
| Discord voice channel | `[What you hear]` — your headphones/speakers |
| Instagram / WhatsApp / Messenger call in a browser | `[What you hear]` |
| Zoom / Teams / Meet | `[What you hear]` |
| In-game voice chat | `[What you hear]` |
| Someone talking in the room | `[Microphone]` — your mic |

**Tip:** if you have several `[What you hear]` entries, pick the device you
actually listen through. The **Input level** bar at the bottom should move when
people talk. If it stays at zero, try a different entry.

### If the call is on your phone (important for Instagram)

The app can only translate audio that **plays on the PC**. An Instagram or
WhatsApp call running on your phone is inaudible to it. Three ways to fix that:

1. **Use the browser version on your PC** where available (Instagram and
   Messenger both support calls at instagram.com / messenger.com). This is the
   simplest option — then just pick `[What you hear]`.
2. **Route the phone call to the PC over Bluetooth.** Pair the phone with
   Windows, and in Windows sound settings enable the phone's "Hands-Free /
   Headset" playback device. Call audio then plays through the PC and the app
   hears it like any other source.
3. **Use Phone Link** (Windows' built-in "Phone Link" app) which can relay calls
   from an Android phone to the PC.

Whichever you choose, confirm the **Input level** bar moves while the other
person talks. If it does, the app can translate it.

### Get the best accuracy — lock the language

In **Settings → Spoken language**, choose the language people actually speak
(Arabic, French, Spanish…) instead of **Auto-detect**.

This is the single biggest accuracy improvement. In testing on real recordings,
short auto-detected clips were sometimes mistaken for a different language
(an Arabic opening was read as Japanese). Locking the language removes that
error completely and is slightly faster.

Leave it on **Auto-detect** for mixed-language servers where people switch
languages mid-call.

---

## Which model should I pick?

Measured on CPU with real Arabic and French speech (see `tests/benchmark_models.py`):

| Model | Speed on a typical CPU | When to use it |
| --- | --- | --- |
| `tiny` | Fastest | Weak/older PCs; rough but usable |
| **`base`** | **Keeps up with live speech** | **Start here** |
| `small` | ~3x slower than realtime — falls behind | Only with an NVIDIA GPU |
| `medium` | GPU needed | High accuracy with a GPU |
| `large-v3` | GPU needed | Best accuracy with a GPU |

If you have an NVIDIA GPU, set **Settings → Processing = cuda** and you can use
`small` or `medium` comfortably. If the GPU can't be initialised the app falls
back to CPU automatically.

If the app prints *"Skipped phrases to stay live"*, your model is too slow —
choose a smaller one. It drops **whole phrases** rather than cutting words in
half, so every subtitle you do see is a clean sentence.

**"Also show the original language text"** runs the model twice per phrase and
doubles CPU cost. It is off by default; turn it on only if you have headroom.

---

## Floating overlay & background mode

- **Drag** the overlay anywhere.
- **Double-click** it to toggle click-through, so it never steals clicks from
  your game or app. Double-click again to make it draggable.
- Font size, number of lines, and fade delay are all in **Settings**.
- **Close to notification area:** closing the main window hides it to the
  Windows notification area (system tray) while translation and floating
  subtitles keep running in the background. Click the tray icon to show the
  window again, or right-click it for **Show**, **Stop translating**, or
  **Quit** (can be toggled in **Settings**).

---

## Spoken English output (optional)

Enable **Speak the English translation out loud** to hear the translation
through Windows' built-in speech voices.

**Use headphones when combining this with `[What you hear]`.** Otherwise the app
hears its own voice. It already pauses listening while it speaks, but headphones
make it reliable.

### Sending translated English into Discord as your microphone

1. Install [VB-Audio Virtual Cable](https://vb-audio.com/Cable/).
2. Enable **Speak the English translation out loud**.
3. Set Windows default output to **CABLE Input**.
4. Set Discord's input device to **CABLE Output**.
5. Select your real microphone as the app's audio source.

---

## Building a standalone `.exe`

**Full instructions: [BUILD_EXE.md](BUILD_EXE.md).** Two routes:

**A. On your PC —** double-click **`build_exe.bat`**. It verifies you have 64-bit
Python, installs everything into an isolated environment, generates the icon,
builds, and opens the output folder. Result:

```
dist\VoiceTranslator\VoiceTranslator.exe
```

**B. Without installing Python —** upload this folder to a GitHub repository,
open the **Actions** tab, and run **"Build Windows EXE"**. GitHub builds it on a
real Windows machine and gives you a zip to download (~10 minutes).

Either way, share the **whole `dist\VoiceTranslator` folder** — the `.exe` needs
the files beside it. Recipients don't need Python. Expect roughly **400–700 MB**;
the speech model downloads separately on first Start so you can change model
size without rebuilding.

> A Windows `.exe` must be built on Windows. PyInstaller cannot cross-compile
> from Linux or macOS, which is why this project ships a builder rather than a
> prebuilt binary.

---

## Where files are stored

| What | Location |
| --- | --- |
| Settings | `%APPDATA%\VoiceTranslator\settings.json` |
| Models | `%APPDATA%\VoiceTranslator\models\` |
| Auto-saved transcripts | `%APPDATA%\VoiceTranslator\transcripts\` |

---

## Troubleshooting

**Start here: double-click `diagnose.bat`.** It checks Python, dependencies,
your audio devices, GPU availability and model loading, then prints exactly what
to fix. It records nothing. Its output is also the most useful thing to share if
you need to ask for help.

**Using the built `.exe` instead?** Run **`selftest.bat`** next to it (or
`VoiceTranslator.exe --selftest`). It verifies the packaged app kept every native
library and the bundled VAD model, and lists your audio sources. Results are
saved to `%APPDATA%\VoiceTranslator\selftest.log`.

**It translates my own voice, not the call** — You picked a `[Microphone]`
source. A microphone hears you and the room; it cannot hear the far end of a
Discord/Zoom call. Stop, and choose an entry starting with **`[What you hear]`**
(for example `[What you hear] Speakers (Realtek Audio)`). The app now warns you
as soon as you select a microphone.

**"Skipped phrase(s)" right after pressing Start** — Older builds queued speech
while the model was still loading and reported it as skipped. The app now waits
until the model is ready and says **"Ready — listening for speech."** Skips
*before* that line should no longer appear. Occasional skips during very long
sentences are normal: the app drops the oldest phrase to stay live.

**I can't see the "Start translating" button** — Fixed: the button bar is now
laid out before the tabs, so it can never be squeezed out. If your window is
very short, the Settings tab scrolls.

**No audio sources listed** — You need Windows, and dependencies installed. Run
`run.bat` again, or `pip install -r requirements.txt`.

**Input level stays at zero** — Wrong output device. Try another
`[What you hear]` entry, and confirm audio is actually playing.

**Words get cut off** — Turn off noise suppression / echo cancellation in
Discord, raise the caller's volume, or try **Response speed = accurate**.

**Translation lags behind the conversation** — Use a smaller model, turn off
"Also show the original language text", or switch to `cuda` with an NVIDIA GPU.

**Wrong language detected** — Set **Spoken language** explicitly in Settings.

**Music or noise produces odd subtitles** — The app gates non-speech using
Whisper's voice activity detection, but sung lyrics are genuine speech-like
audio and may be transcribed. Pause translation during music.

---

## How it works

```
system audio (WASAPI loopback)
   -> resample to 16 kHz mono
   -> voice activity detection, split into phrases at natural pauses
   -> faster-whisper "translate" task  ->  English
   -> subtitles + overlay + optional spoken output
```

Translation runs on its own thread with a bounded queue, so a slow model can
never garble incoming audio: it drops the oldest complete phrase and warns you.

Whisper's `translate` task goes straight from the source language to English,
which is why no separate translation service is required.

---

## Tests

```bash
python tests/test_core.py                   # segmentation, resampling, filtering (no hardware)
python tests/test_recognition_and_speed.py  # fast phrase-end detection, quiet speech, decoding
python tests/test_gui_smoke.py              # GUI wiring, headless
python tests/test_tray_background.py        # notification area Show / Stop / Quit & background mode
python tests/test_lifecycle.py              # start/stop race conditions, headless
python tests/test_build_spec.py             # .exe recipe: bundled assets, icon, workflow
python main.py --selftest                   # speech pipeline intact (works in the .exe too)
python tests/test_fixes.py                  # regression tests for accuracy/backlog fixes
python tests/test_integration.py            # real speech -> English (downloads tiny model)
python tests/benchmark_models.py            # speed + accuracy per model size
```

Large audio samples are not included. To fetch them:

```bash
python tests/samples/fetch_samples.py
python tests/samples/fetch_more.py
```

---

## Privacy and responsible use

All processing is local; no audio or text is sent to any server (the only
network access is the one-time model download).

Recording or translating a conversation may be regulated where you live, and
some platforms' terms restrict it. Tell people they are being translated and
get consent where required.
