"""Audio capture for Windows: WASAPI loopback (what you hear) and microphones.

Loopback capture is the key feature: it records whatever is playing through
your speakers/headphones, so it works with Discord voice channels, Instagram
or WhatsApp calls in a browser, Zoom, Teams, games, videos — anything.
"""
from __future__ import annotations

import math
import queue
import threading
from dataclasses import dataclass

import numpy as np

try:
    import pyaudiowpatch as pyaudio

    HAS_PYAUDIO = True
except Exception:  # pragma: no cover - non-Windows or missing dependency
    pyaudio = None
    HAS_PYAUDIO = False

try:
    from scipy.signal import resample_poly

    HAS_SCIPY = True
except Exception:  # pragma: no cover
    HAS_SCIPY = False

TARGET_RATE = 16000


@dataclass
class Device:
    index: int
    name: str
    kind: str  # 'loopback' | 'mic'
    rate: int
    channels: int
    is_default: bool = False

    @property
    def label(self) -> str:
        tag = "What you hear" if self.kind == "loopback" else "Microphone"
        star = "  ★ default" if self.is_default else ""
        return f"[{tag}] {self.name}{star}"


def _clean(name: str) -> str:
    return name.replace(" [Loopback]", "").strip()


def list_devices() -> list[Device]:
    """Return loopback devices first (recommended), then WASAPI microphones."""
    if not HAS_PYAUDIO:
        return []

    devices: list[Device] = []
    p = pyaudio.PyAudio()
    try:
        default_loop_index = None
        try:
            default_loop = p.get_default_wasapi_loopback()
            default_loop_index = default_loop["index"]
        except Exception:
            pass

        try:
            for d in p.get_loopback_device_info_generator():
                devices.append(
                    Device(
                        index=int(d["index"]),
                        name=_clean(str(d["name"])),
                        kind="loopback",
                        rate=int(d["defaultSampleRate"]),
                        channels=max(1, int(d["maxInputChannels"])),
                        is_default=(int(d["index"]) == default_loop_index),
                    )
                )
        except Exception:
            pass

        wasapi_index = None
        try:
            wasapi_index = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        except Exception:
            pass

        default_mic_index = None
        try:
            default_mic_index = p.get_default_input_device_info()["index"]
        except Exception:
            pass

        for i in range(p.get_device_count()):
            try:
                d = p.get_device_info_by_index(i)
            except Exception:
                continue
            if int(d.get("maxInputChannels", 0)) <= 0:
                continue
            if d.get("isLoopbackDevice"):
                continue
            if wasapi_index is not None and d.get("hostApi") != wasapi_index:
                continue
            devices.append(
                Device(
                    index=int(d["index"]),
                    name=_clean(str(d["name"])),
                    kind="mic",
                    rate=int(d["defaultSampleRate"]),
                    channels=max(1, int(d["maxInputChannels"])),
                    is_default=(int(d["index"]) == default_mic_index),
                )
            )
    finally:
        try:
            p.terminate()
        except Exception:
            pass
    return devices


class Resampler:
    """Convert native device sample rate to 16 kHz mono float32."""

    def __init__(self, src_rate: int, dst_rate: int = TARGET_RATE) -> None:
        g = math.gcd(int(src_rate), int(dst_rate)) or 1
        self.up = int(dst_rate) // g
        self.down = int(src_rate) // g

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.up == 1 and self.down == 1:
            return x.astype(np.float32, copy=False)
        if HAS_SCIPY:
            return resample_poly(x, self.up, self.down).astype(np.float32)
        n = max(1, int(round(len(x) * self.up / self.down)))
        return np.interp(
            np.linspace(0, len(x) - 1, n), np.arange(len(x)), x
        ).astype(np.float32)


class AudioCapture:
    """Streams mono float32 chunks (native rate) into a queue."""

    def __init__(self, device: Device, out_queue: "queue.Queue[np.ndarray]",
                 on_error=None) -> None:
        self.device = device
        self.out = out_queue
        self.on_error = on_error
        self._p = None
        self._stream = None
        self._lock = threading.Lock()
        self.running = False

    def _callback(self, in_data, frame_count, time_info, status):
        try:
            arr = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            ch = self.device.channels
            if ch > 1:
                usable = (len(arr) // ch) * ch
                arr = arr[:usable].reshape(-1, ch).mean(axis=1)
            try:
                self.out.put_nowait(arr)
            except queue.Full:
                pass  # drop rather than block the audio callback
        except Exception as exc:  # pragma: no cover
            if self.on_error:
                self.on_error(f"Audio callback error: {exc}")
        return (None, pyaudio.paContinue)

    def start(self) -> None:
        if not HAS_PYAUDIO:
            raise RuntimeError(
                "PyAudioWPatch is not installed. Audio capture requires Windows.\n"
                "Install dependencies with: pip install -r requirements.txt"
            )
        with self._lock:
            self._p = pyaudio.PyAudio()
            self._stream = self._p.open(
                format=pyaudio.paInt16,
                channels=self.device.channels,
                rate=self.device.rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=512,
                stream_callback=self._callback,
            )
            self._stream.start_stream()
            self.running = True

    def stop(self) -> None:
        with self._lock:
            self.running = False
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                except Exception:
                    pass
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._p is not None:
                try:
                    self._p.terminate()
                except Exception:
                    pass
                self._p = None
