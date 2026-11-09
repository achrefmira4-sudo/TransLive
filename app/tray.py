"""Windows notification-area (system tray) icon and context menu.

Allows Voice Translator to keep capturing and translating in the background
when the main window is closed, with quick tray controls to:
  * Show the main window (left-click, double-click, or menu -> Show)
  * Stop translating
  * Quit the application completely

Implemented directly against Win32 Shell_NotifyIconW via ctypes so no extra
third-party packages are required at runtime or in the packaged .exe. On
non-Windows platforms (or headless sessions without a taskbar shell), the tray
cleanly reports `available = False` so closing the window falls back to a
normal exit instead of leaving a hidden window with no controls.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class MenuItem:
    label: str = ""
    action: str = ""
    enabled: bool = True
    default: bool = False
    separator: bool = False


def find_icon_path(explicit: str | None = None) -> str | None:
    """Locate icon.ico whether running from source or a PyInstaller bundle."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "icon.ico"))
    if getattr(sys, "frozen", False):
        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "icon.ico")
        )
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(repo_root, "icon.ico"))

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


class TrayIcon:
    """Notification-area controller with pluggable OS backend."""

    def __init__(
        self,
        title: str = "Voice Translator",
        icon_path: str | None = None,
        on_show: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        is_translating: Callable[[], bool] | None = None,
        backend=None,
    ) -> None:
        self.title = title
        self.tooltip = title
        self.icon_path = find_icon_path(icon_path)
        self.on_show = on_show
        self.on_stop = on_stop
        self.on_quit = on_quit
        self.is_translating = is_translating
        self._backend = backend
        self._started = False

    @property
    def available(self) -> bool:
        return bool(self._started and self._backend is not None
                    and self._backend.available)

    def start(self) -> bool:
        if self._started:
            return self.available
        if self._backend is None and sys.platform == "win32":
            self._backend = _Win32TrayBackend(self)
        if self._backend is None:
            return False
        self._started = True
        ok = bool(self._backend.start())
        if not ok:
            self._started = False
        return ok

    def stop(self) -> None:
        backend = self._backend
        self._started = False
        if backend is not None:
            try:
                backend.stop()
            except Exception:
                pass

    def set_status(self, status: str) -> None:
        text = f"{self.title} — {status}" if status else self.title
        self.tooltip = text[:127]
        if self.available and self._backend is not None:
            try:
                self._backend.set_tooltip(self.tooltip)
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        if self.available and self._backend is not None:
            try:
                self._backend.notify(title[:63], message[:255])
            except Exception:
                pass

    def menu_items(self) -> list[MenuItem]:
        running = False
        if self.is_translating is not None:
            try:
                running = bool(self.is_translating())
            except Exception:
                running = False
        return [
            MenuItem(label="Show Voice Translator", action="show",
                     enabled=True, default=True),
            MenuItem(label="Stop translating", action="stop",
                     enabled=running),
            MenuItem(separator=True),
            MenuItem(label="Quit", action="quit", enabled=True),
        ]

    def trigger(self, action: str) -> None:
        if action == "show" and self.on_show is not None:
            self.on_show()
        elif action == "stop" and self.on_stop is not None:
            self.on_stop()
        elif action == "quit" and self.on_quit is not None:
            self.on_quit()


# ---------------------------------------------------------------------------
# Win32 Shell_NotifyIconW implementation (loaded only on Windows)
# ---------------------------------------------------------------------------

if sys.platform == "win32":  # pragma: no cover - exercised on Windows
    import ctypes
    from ctypes import wintypes

    LRESULT = ctypes.c_ssize_t
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM
    )

    WM_NULL = 0x0000
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_CONTEXTMENU = 0x007B
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_USER = 0x0400
    WM_TRAY = WM_USER + 20

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002

    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    NIF_INFO = 0x00000010
    NIIF_INFO = 0x00000001
    NIIF_NOSOUND = 0x00000010

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010
    LR_DEFAULTSIZE = 0x00000040
    IDI_APPLICATION = 32512

    MF_STRING = 0x00000000
    MF_GRAYED = 0x00000001
    MF_DISABLED = 0x00000002
    MF_SEPARATOR = 0x00000800

    TPM_RIGHTBUTTON = 0x0002
    TPM_BOTTOMALIGN = 0x0020
    TPM_NONOTIFY = 0x0080
    TPM_RETURNCMD = 0x0100

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", _GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class _WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32
    _kernel32 = ctypes.windll.kernel32

    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, WPARAM, LPARAM
    ]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
    _user32.RegisterClassExW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
    ]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = LRESULT
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, WPARAM, LPARAM
    ]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.restype = None
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT

    _user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _user32.DestroyIcon.restype = wintypes.BOOL

    _user32.CreatePopupMenu.argtypes = []
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR
    ]
    _user32.AppendMenuW.restype = wintypes.BOOL
    _user32.SetMenuDefaultItem.argtypes = [
        wintypes.HMENU, wintypes.UINT, wintypes.UINT
    ]
    _user32.SetMenuDefaultItem.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.HWND, wintypes.LPCVOID,
    ]
    _user32.TrackPopupMenu.restype = wintypes.BOOL
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.DestroyMenu.restype = wintypes.BOOL

    _shell32.Shell_NotifyIconW.argtypes = [
        wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)
    ]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _shell32.ExtractIconW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT
    ]
    _shell32.ExtractIconW.restype = wintypes.HICON

    class _Win32TrayBackend:
        def __init__(self, owner: TrayIcon) -> None:
            self._owner = owner
            self.available = False
            self._hwnd: wintypes.HWND | None = None
            self._hicon: wintypes.HICON | None = None
            self._owns_icon = False
            self._hinstance = _kernel32.GetModuleHandleW(None)
            self._class_name = f"VoiceTranslatorTray_{id(self)}"
            self._taskbar_msg = _user32.RegisterWindowMessageW("TaskbarCreated")
            self._wndproc = WNDPROC(self._on_message)
            self._lock = threading.Lock()
            self._ready = threading.Event()
            self._thread: threading.Thread | None = None

        def start(self) -> bool:
            if self._thread is not None:
                return self.available
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, name="tray-icon", daemon=True
            )
            self._thread.start()
            self._ready.wait(timeout=3.0)
            return self.available

        def stop(self) -> None:
            with self._lock:
                hwnd = self._hwnd
            if hwnd:
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            if self._thread is not None and self._thread is not threading.current_thread():
                self._thread.join(timeout=2.0)
            self._thread = None
            self.available = False

        def set_tooltip(self, tooltip: str) -> None:
            with self._lock:
                if not self._hwnd or not self.available:
                    return
                nid = self._make_nid(NIF_TIP)
                nid.szTip = tooltip[:127]
                _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        def notify(self, title: str, message: str) -> None:
            with self._lock:
                if not self._hwnd or not self.available:
                    return
                nid = self._make_nid(NIF_INFO)
                nid.szInfoTitle = title[:63]
                nid.szInfo = message[:255]
                nid.dwInfoFlags = NIIF_INFO | NIIF_NOSOUND
                _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        def _load_icon(self) -> wintypes.HICON:
            path = self._owner.icon_path
            if path and os.path.exists(path):
                h = _user32.LoadImageW(
                    None, path, IMAGE_ICON, 0, 0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE,
                )
                if h:
                    self._owns_icon = True
                    return h
            exe_icon = _shell32.ExtractIconW(
                self._hinstance, sys.executable, 0
            )
            if exe_icon and exe_icon != 1:
                self._owns_icon = True
                return exe_icon
            self._owns_icon = False
            return _user32.LoadIconW(
                None, ctypes.cast(IDI_APPLICATION, wintypes.LPCWSTR)
            )

        def _make_nid(self, flags: int) -> _NOTIFYICONDATAW:
            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = flags
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self._hicon
            nid.szTip = self._owner.tooltip[:127]
            return nid

        def _add_icon(self) -> bool:
            nid = self._make_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
            return bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

        def _delete_icon(self) -> None:
            if self._hwnd:
                nid = self._make_nid(0)
                _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

        def _run(self) -> None:
            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = self._hinstance
            wc.lpszClassName = self._class_name

            atom = _user32.RegisterClassExW(ctypes.byref(wc))
            if not atom:
                self._ready.set()
                return

            hwnd = _user32.CreateWindowExW(
                0, self._class_name, "VoiceTranslatorTray", 0,
                0, 0, 0, 0, None, None, self._hinstance, None,
            )
            if not hwnd:
                _user32.UnregisterClassW(self._class_name, self._hinstance)
                self._ready.set()
                return

            with self._lock:
                self._hwnd = hwnd
                self._hicon = self._load_icon()
                added = self._add_icon()
                self.available = added

            if not added:
                with self._lock:
                    self._hwnd = None
                _user32.DestroyWindow(hwnd)
                if self._owns_icon and self._hicon:
                    _user32.DestroyIcon(self._hicon)
                    self._hicon = None
                _user32.UnregisterClassW(self._class_name, self._hinstance)
                self._ready.set()
                return

            self._ready.set()

            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

            with self._lock:
                self.available = False
                self._hwnd = None
                if self._owns_icon and self._hicon:
                    _user32.DestroyIcon(self._hicon)
                self._hicon = None
            _user32.UnregisterClassW(self._class_name, self._hinstance)

        def _open_context_menu(self, hwnd: wintypes.HWND) -> None:
            items = self._owner.menu_items()
            hmenu = _user32.CreatePopupMenu()
            if not hmenu:
                return
            cmd_map: dict[int, str] = {}
            try:
                next_id = 1
                for item in items:
                    if item.separator:
                        _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                        continue
                    flags = MF_STRING
                    if not item.enabled:
                        flags |= MF_GRAYED | MF_DISABLED
                    cmd_id = next_id
                    next_id += 1
                    cmd_map[cmd_id] = item.action
                    _user32.AppendMenuW(hmenu, flags, cmd_id, item.label)
                    if item.default:
                        _user32.SetMenuDefaultItem(hmenu, cmd_id, 0)

                pt = wintypes.POINT()
                _user32.GetCursorPos(ctypes.byref(pt))
                _user32.SetForegroundWindow(hwnd)
                selected = int(_user32.TrackPopupMenu(
                    hmenu,
                    TPM_RIGHTBUTTON | TPM_BOTTOMALIGN | TPM_NONOTIFY | TPM_RETURNCMD,
                    pt.x, pt.y, 0, hwnd, None,
                ))
                _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            finally:
                _user32.DestroyMenu(hmenu)

            action = cmd_map.get(selected)
            if action:
                self._owner.trigger(action)

        def _on_message(self, hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                ev = int(lparam) & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._owner.trigger("show")
                elif ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._open_context_menu(hwnd)
                return 0
            if self._taskbar_msg and msg == self._taskbar_msg:
                # Re-register the tray icon if Explorer restarted.
                with self._lock:
                    if self._hwnd:
                        self._add_icon()
                return 0
            if msg == WM_CLOSE:
                with self._lock:
                    self._delete_icon()
                    self.available = False
                _user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
