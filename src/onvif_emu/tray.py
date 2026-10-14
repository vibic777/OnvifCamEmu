"""Windows notification-area (system tray) icon for the console-less GUI build.

The windowed exe (``console=False``) has no window and no taskbar entry, so once the
browser tab is closed the only sign it is still running is Task Manager. This module
adds a real tray icon (Shell_NotifyIcon via ctypes — no extra dependency) with a right
click menu to reopen the web GUI or exit, so the running service is always visible and
reachable from the notification area, the way any other background service is.

Windows only; a no-op everywhere else and if anything about the tray fails to set up
(this must never take the emulator down). All win32 handles are pointer-sized (HWND /
HICON / HMENU) — argtypes/restype are set explicitly on every call because ctypes'
default 32-bit-int guess corrupts handles on 64-bit Windows.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

_WM_TRAYICON = 0x8000 + 20  # WM_APP + 20
_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_COMMAND = 0x0111
_WM_LBUTTONUP = 0x0202
_WM_RBUTTONUP = 0x0205
_WM_NULL = 0x0000

_NIM_ADD = 0x00000000
_NIM_MODIFY = 0x00000001
_NIM_DELETE = 0x00000002
_NIF_MESSAGE = 0x00000001
_NIF_ICON = 0x00000002
_NIF_TIP = 0x00000004

_ID_OPEN = 1001
_ID_EXIT = 1002

_WS_EX_TOOLWINDOW = 0x00000080
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_LR_DEFAULTSIZE = 0x00000040
_MF_SEPARATOR = 0x00000800
_TPM_RIGHTBUTTON = 0x0002

_state: _Tray | None = None
_lock = threading.Lock()


def is_supported() -> bool:
    return sys.platform == "win32"


def ensure_started(*, icon_path: Path, tooltip: str, url_provider: Callable[[], str], on_exit: Callable[[], None]) -> None:
    """Start the tray icon once per process. Safe to call repeatedly / off-Windows."""
    global _state
    if not is_supported():
        return
    with _lock:
        if _state is not None:
            _state.set_tooltip(tooltip)
            return
        try:
            _state = _Tray(icon_path, tooltip, url_provider, on_exit)
            _state.start()
        except Exception:  # noqa: BLE001 - the tray is cosmetic, never fatal
            log.exception("tray icon failed to start")
            _state = None


def set_tooltip(tooltip: str) -> None:
    if _state is not None:
        _state.set_tooltip(tooltip)


def stop() -> None:
    global _state
    with _lock:
        if _state is not None:
            _state.stop()
            _state = None


class _Tray:
    def __init__(self, icon_path: Path, tooltip: str, url_provider: Callable[[], str], on_exit: Callable[[], None]):
        self.icon_path = icon_path
        self.tooltip = tooltip[:127]
        self.url_provider = url_provider
        self.on_exit = on_exit
        self.thread: threading.Thread | None = None
        self.hwnd = 0
        self.hicon = 0
        self._nid_type = None
        self._ready = threading.Event()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)
        self.thread.start()
        self._ready.wait(timeout=3.0)

    def set_tooltip(self, tooltip: str) -> None:
        self.tooltip = tooltip[:127]
        if self.hwnd:
            self._notify(_NIM_MODIFY)

    def stop(self) -> None:
        import ctypes

        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, _WM_CLOSE, 0, 0)

    # -- Win32 message loop (its own thread) ---------------------------------
    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
            ]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON),
            ]

        # Pointer-sized handles are truncated by ctypes' default 32-bit-int guess on
        # 64-bit Windows unless argtypes/restype are declared explicitly.
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_longlong
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostQuitMessage.restype = None
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_longlong
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_void_p, wintypes.LPCWSTR]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
        ]
        user32.TrackPopupMenu.restype = wintypes.BOOL
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == _WM_TRAYICON:
                if lparam in (_WM_LBUTTONUP, _WM_RBUTTONUP):
                    self._show_menu(hwnd)
                return 0
            if msg == _WM_COMMAND:
                cmd = wparam & 0xFFFF
                if cmd == _ID_OPEN:
                    try:
                        import webbrowser

                        webbrowser.open(self.url_provider())
                    except Exception:
                        log.exception("tray: open GUI failed")
                elif cmd == _ID_EXIT:
                    try:
                        self.on_exit()
                    except Exception:
                        log.exception("tray: exit callback failed")
                    user32.DestroyWindow(hwnd)
                return 0
            if msg == _WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == _WM_DESTROY:
                self._notify(_NIM_DELETE)
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # keep alive for the thread's lifetime
        hinst = kernel32.GetModuleHandleW(None)
        class_name = "OnvifEmulatorTray"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))

        # No HWND_MESSAGE trick (a negative handle mis-marshals easily): a plain
        # top-level window that is never shown never gets a taskbar entry either.
        self.hwnd = user32.CreateWindowExW(
            _WS_EX_TOOLWINDOW, class_name, "ONVIF Emulator", 0, 0, 0, 0, 0, None, None, hinst, None
        )
        if not self.hwnd:
            self._ready.set()
            return

        self._nid_type = NOTIFYICONDATAW
        icon = user32.LoadImageW(None, str(self.icon_path), _IMAGE_ICON, 0, 0, _LR_LOADFROMFILE | _LR_DEFAULTSIZE)
        self.hicon = icon or 0
        self._notify(_NIM_ADD)
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = 0

    def _notify(self, action: int) -> None:
        import ctypes

        shell32 = ctypes.windll.shell32
        nid = self._nid_type()
        nid.cbSize = ctypes.sizeof(self._nid_type)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
        nid.uCallbackMessage = _WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip
        shell32.Shell_NotifyIconW(action, ctypes.byref(nid))

    def _show_menu(self, hwnd: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, _ID_OPEN, "Открыть веб-интерфейс")
        user32.AppendMenuW(menu, _MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, 0, _ID_EXIT, "Выход")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        user32.TrackPopupMenu(menu, _TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, _WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
