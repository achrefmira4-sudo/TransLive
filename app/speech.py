"""Optional spoken English output via Windows SAPI (pyttsx3).

Runs on its own thread because pyttsx3's SAPI driver is not thread-safe and
must be created and used on the same thread. While it speaks, the pipeline
pauses listening so the app does not translate its own voice.
"""
from __future__ import annotations

import queue
import threading


class Speaker:
    def __init__(self, rate: int = 175, on_speaking=None) -> None:
        self.rate = rate
        self.on_speaking = on_speaking
        self._q: "queue.Queue[str | None]" = queue.Queue(maxsize=8)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._engine = None
        self.enabled = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)
            # Prefer an English voice if one is installed.
            try:
                for v in self._engine.getProperty("voices"):
                    blob = f"{v.id} {v.name} {getattr(v, 'languages', '')}".lower()
                    if "en-us" in blob or "en_us" in blob or "english" in blob:
                        self._engine.setProperty("voice", v.id)
                        break
            except Exception:
                pass
        except Exception:
            self._engine = None
            return

        while not self._stop.is_set():
            try:
                text = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            if not self.enabled:
                continue
            try:
                if self.on_speaking:
                    self.on_speaking(True)
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:
                pass
            finally:
                if self.on_speaking:
                    self.on_speaking(False)

    def set_rate(self, rate: int) -> None:
        self.rate = rate
        if self._engine is not None:
            try:
                self._engine.setProperty("rate", rate)
            except Exception:
                pass

    def say(self, text: str) -> None:
        if not self.enabled or not text:
            return
        try:
            self._q.put_nowait(text)
        except queue.Full:
            # Drop the oldest line so speech keeps up with the conversation.
            try:
                self._q.get_nowait()
                self._q.put_nowait(text)
            except Exception:
                pass

    def clear(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._stop.set()
        self.clear()
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass
            self._engine = None
