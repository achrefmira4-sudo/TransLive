"""Tests for background operation in the Windows notification area (tray).

Verifies:
  * Tray menu exposes Show, Stop translating (enabled only while running),
    and Quit controls.
  * Closing the main window hides it to the notification area while keeping
    live translation and floating subtitles running in the background.
  * Tray Show restores and focuses the main window without interrupting the
    live pipeline.
  * Tray Stop halts translation and hides the overlay while keeping the app
    in the notification area.
  * Tray Quit cleanly stops the tray icon, stops active sessions, preserves
    the last visible window geometry, and destroys the UI.
  * When tray background mode is disabled or unavailable, closing the window
    exits cleanly instead of leaving an unreachable hidden window.

Run:  python tests/test_tray_background.py
  or: pytest tests/test_tray_background.py -q
"""
from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_gui_smoke import _install_fake_tk

from app.config import app_dir
from app.pipeline import Event
from app.tray import TrayIcon, find_icon_path


class FakeTrayBackend:
    """In-memory tray backend to test notification-area behaviour anywhere."""

    def __init__(self, should_succeed: bool = True) -> None:
        self.should_succeed = should_succeed
        self.available = False
        self.started = False
        self.stopped = False
        self.tooltips: list[str] = []
        self.notifications: list[tuple[str, str]] = []

    def start(self) -> bool:
        self.started = True
        self.available = self.should_succeed
        return self.available

    def stop(self) -> None:
        self.stopped = True
        self.available = False

    def set_tooltip(self, tooltip: str) -> None:
        self.tooltips.append(tooltip)

    def notify(self, title: str, message: str) -> None:
        self.notifications.append((title, message))


class FakeRunningPipeline:
    def __init__(self) -> None:
        self.running = True
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        self.running = False


def _make_app(tray_available: bool = True):
    _install_fake_tk()
    import main as app_main

    root = MagicMock(name="root")
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080
    root.geometry.return_value = "1020x780+120+80"

    def _after(ms, fn=None, *a):
        if ms == 0 and fn is not None:
            fn(*a)
        return "after-id"

    root.after.side_effect = _after

    app = app_main.App(root)
    app.overlay.win.geometry.return_value = "1100x150+400+800"

    backend = FakeTrayBackend(should_succeed=tray_available)
    app.tray = TrayIcon(
        title="Voice Translator",
        on_show=lambda: app._enqueue_tray_action("show"),
        on_stop=lambda: app._enqueue_tray_action("stop"),
        on_quit=lambda: app._enqueue_tray_action("quit"),
        is_translating=app.is_translating,
        backend=backend,
    )
    app.tray.start()
    return app, root, backend


def test_icon_discovery():
    icon = find_icon_path()
    assert icon is not None and os.path.exists(icon) and icon.endswith("icon.ico")


def test_tray_menu_items_reflect_translation_state():
    running = {"active": False}
    tray = TrayIcon(
        is_translating=lambda: running["active"],
        backend=FakeTrayBackend(True),
    )
    assert tray.start() is True

    idle_items = [i for i in tray.menu_items() if not i.separator]
    actions = {i.action: i for i in idle_items}
    assert set(actions.keys()) == {"show", "stop", "quit"}
    assert actions["show"].enabled is True and actions["show"].default is True
    assert actions["stop"].enabled is False
    assert actions["quit"].enabled is True

    running["active"] = True
    live_actions = {i.action: i for i in tray.menu_items() if not i.separator}
    assert live_actions["stop"].enabled is True


def test_close_window_hides_to_tray_and_keeps_translating():
    app, root, backend = _make_app(tray_available=True)
    pipe = FakeRunningPipeline()
    app.pipeline = pipe
    app.overlay_var.set(True)
    app.close_to_tray_var.set(True)

    # User clicks window close [X] while translating.
    app.on_close()

    assert app.window_hidden is True
    assert root.withdraw.called, "main window should withdraw to notification area"
    assert not root.destroy.called, "window close must not quit while tray is active"
    assert pipe.running is True and pipe.stop_calls == 0
    assert len(backend.notifications) == 1
    assert "background" in backend.notifications[0][1].lower()

    # Subsequent translations keep arriving and updating overlay in background.
    app.overlay.win.deiconify.reset_mock()
    app.events.put(Event(
        "translation",
        text="Good evening everyone.",
        original="Bonsoir à tous.",
        language="fr",
        probability=0.96,
    ))
    app._drain_events()
    assert any("Good evening everyone." in ln for ln in app.transcript_lines)
    assert backend.tooltips and "Listening" in backend.tooltips[-1]


def test_tray_show_and_stop_controls():
    app, root, backend = _make_app(tray_available=True)
    pipe = FakeRunningPipeline()
    app.pipeline = pipe
    app.close_to_tray_var.set(True)

    # Hide to tray while translating.
    app.on_close()
    assert app.window_hidden is True

    # 1. Tray -> Show restores the main window without stopping translation.
    app.tray.trigger("show")
    app._drain_events()
    assert app.window_hidden is False
    assert root.deiconify.called
    assert root.lift.called
    assert pipe.running is True

    # Hide again, then use Tray -> Stop to stop translating in the background.
    app.on_close()
    assert app.window_hidden is True
    app.overlay.win.withdraw.reset_mock()

    app.tray.trigger("stop")
    app._drain_events()
    deadline = time.time() + 3.0
    while app._stopping and time.time() < deadline:
        time.sleep(0.02)

    assert pipe.stop_calls == 1
    assert app.is_translating() is False
    assert app.overlay.win.withdraw.called, "stopping should hide subtitle overlay"
    assert app.window_hidden is True, "Stop should stay in tray without exiting"
    assert not root.destroy.called


def test_tray_quit_exits_and_preserves_visible_geometry():
    app, root, backend = _make_app(tray_available=True)
    pipe = FakeRunningPipeline()
    app.pipeline = pipe
    app.close_to_tray_var.set(True)

    # Visible geometry before hiding.
    root.geometry.return_value = "1080x760+50+60"
    app.on_close()
    assert app.window_hidden is True

    # Simulate Tk returning a dummy unmapped geometry while withdrawn.
    root.geometry.return_value = "1x1+0+0"

    app.tray.trigger("quit")
    app._drain_events()

    assert backend.stopped is True
    assert pipe.stop_calls == 1
    assert root.destroy.called

    saved = json.loads((app_dir() / "settings.json").read_text(encoding="utf-8"))
    assert saved["window_geometry"] == "1080x760+50+60", saved["window_geometry"]


def test_close_quits_when_tray_disabled_or_unavailable():
    # Case A: user unchecked "Keep running in the notification area..."
    app1, root1, _ = _make_app(tray_available=True)
    pipe1 = FakeRunningPipeline()
    app1.pipeline = pipe1
    app1.close_to_tray_var.set(False)
    app1.on_close()
    assert pipe1.stop_calls == 1
    assert root1.destroy.called

    # Case B: OS has no notification area available -> never hide unreachable UI
    app2, root2, _ = _make_app(tray_available=False)
    pipe2 = FakeRunningPipeline()
    app2.pipeline = pipe2
    app2.close_to_tray_var.set(True)
    app2.on_close()
    assert pipe2.stop_calls == 1
    assert root2.destroy.called


def test_win32_backend_contract_and_dispatch():
    import ctypes
    import importlib
    import app.tray as tray_mod

    if sys.platform == "win32":
        # Real 64-bit Windows ABI struct sizes.
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            assert ctypes.sizeof(tray_mod._NOTIFYICONDATAW) == 976
            assert ctypes.sizeof(tray_mod._WNDCLASSEXW) == 80
        return

    # On non-Windows, exercise the Win32 backend message/menu logic against
    # mocked user32/shell32/kernel32 so typos and ID mappings fail here too.
    had_winfunctype = hasattr(ctypes, "WINFUNCTYPE")
    had_windll = hasattr(ctypes, "windll")
    orig_platform = sys.platform
    windll = MagicMock(name="windll")
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    ctypes.windll = windll

    u32 = windll.user32
    sh32 = windll.shell32
    u32.RegisterWindowMessageW.return_value = 0xC042
    u32.CreatePopupMenu.return_value = 0x4444
    sh32.Shell_NotifyIconW.return_value = 1

    try:
        sys.platform = "win32"
        importlib.reload(tray_mod)

        fired: list[str] = []
        icon = tray_mod.TrayIcon(
            title="Voice Translator",
            on_show=lambda: fired.append("show"),
            on_stop=lambda: fired.append("stop"),
            on_quit=lambda: fired.append("quit"),
            is_translating=lambda: True,
        )
        be = tray_mod._Win32TrayBackend(icon)
        be._hwnd = 0x2222
        be.available = True

        be._on_message(0x2222, tray_mod.WM_TRAY, 1, tray_mod.WM_LBUTTONUP)
        assert fired == ["show"]

        u32.TrackPopupMenu.return_value = 2
        be._on_message(0x2222, tray_mod.WM_TRAY, 1, tray_mod.WM_RBUTTONUP)
        assert fired == ["show", "stop"]

        u32.TrackPopupMenu.return_value = 3
        be._on_message(0x2222, tray_mod.WM_TRAY, 1, tray_mod.WM_CONTEXTMENU)
        assert fired == ["show", "stop", "quit"]

        sh32.Shell_NotifyIconW.reset_mock()
        be._on_message(0x2222, 0xC042, 0, 0)
        assert sh32.Shell_NotifyIconW.called

        be.set_tooltip("Voice Translator — Listening")
        be.notify("Voice Translator", "Still translating")

        be._on_message(0x2222, tray_mod.WM_CLOSE, 0, 0)
        assert u32.DestroyWindow.called
        assert be.available is False
    finally:
        sys.platform = orig_platform
        if not had_winfunctype and hasattr(ctypes, "WINFUNCTYPE"):
            delattr(ctypes, "WINFUNCTYPE")
        if not had_windll and hasattr(ctypes, "windll"):
            delattr(ctypes, "windll")
        importlib.reload(tray_mod)


def _run_all() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
