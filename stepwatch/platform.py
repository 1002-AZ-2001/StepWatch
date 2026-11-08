"""Windows window enumeration and thread-owned MSS screen capture.

Importing this module does not install hooks, open capture resources or require
Windows. Windows calls and MSS initialization are deliberately lazy.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import threading
from typing import Callable

from .models import CaptureTarget, Rect


class CaptureUnavailable(RuntimeError):
    """The selected target cannot currently be captured."""


def enable_dpi_awareness() -> bool:
    """Use physical pixels on mixed-DPI desktops; call before creating the GUI."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        try:
            function = user32.SetProcessDpiAwarenessContext
            function.argtypes = [ctypes.c_void_p]
            function.restype = wintypes.BOOL
            if function(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
                return True
        except AttributeError:
            pass
        try:
            shcore = ctypes.WinDLL("shcore", use_last_error=True)
            shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            shcore.SetProcessDpiAwareness.restype = ctypes.c_long
            if shcore.SetProcessDpiAwareness(2) == 0:
                return True
        except (AttributeError, OSError):
            pass
        user32.SetProcessDPIAware.argtypes = []
        user32.SetProcessDPIAware.restype = wintypes.BOOL
        return bool(user32.SetProcessDPIAware())
    except OSError:
        return False


#: Windows defaults: 500 ms and a 4 x 4 pixel rectangle around the first press.
DEFAULT_DOUBLE_CLICK = (0.5, 2, 2)


def double_click_settings() -> tuple[float, int, int]:
    """Double-click time (s) and half-size (px) of the double-click rectangle."""
    if sys.platform != "win32":
        return DEFAULT_DOUBLE_CLICK
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetDoubleClickTime.argtypes = []
        user32.GetDoubleClickTime.restype = wintypes.UINT
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        seconds = user32.GetDoubleClickTime() / 1000
        width, height = user32.GetSystemMetrics(36), user32.GetSystemMetrics(37)  # SM_C?DOUBLECLK
    except (AttributeError, OSError):
        return DEFAULT_DOUBLE_CLICK
    if seconds <= 0 or width <= 0 or height <= 0:
        return DEFAULT_DOUBLE_CLICK
    return seconds, max(1, width // 2), max(1, height // 2)


class _NativeWindows:
    """Small typed Win32 adapter; an adapter can be injected into the backend."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise CaptureUnavailable("La sélection de fenêtres nécessite Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            "EnumWindows": ([self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsIconic": ([wintypes.HWND], wintypes.BOOL),
            "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
            "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetWindow": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_short),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user32, name)
            function.argtypes = arguments
            function.restype = result
        try:
            self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
            self.dwm.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            self.dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        except OSError:
            self.dwm = None

    def is_window(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindow(hwnd))

    def is_visible(self, hwnd: int) -> bool:
        if not self.user32.IsWindowVisible(hwnd):
            return False
        if self.dwm:
            cloaked = wintypes.DWORD()
            if (self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked),
                                               ctypes.sizeof(cloaked)) == 0
                    and cloaked.value):
                return False
        return True

    def mouse_button_down(self) -> bool:
        """True while the left, right or middle physical button is held.

        Polled by the hook watchdog; independent of the low-level hook. The
        most significant bit means "currently down" (the low bit is unusable
        across processes).
        """
        return any(self.user32.GetAsyncKeyState(key) & 0x8000
                   for key in (0x01, 0x02, 0x04))  # VK_LBUTTON, VK_RBUTTON, VK_MBUTTON

    def is_minimized(self, hwnd: int) -> bool:
        return bool(self.user32.IsIconic(hwnd))

    def enumerate_windows(self) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []

        @self.callback_type
        def callback(hwnd: int, _parameter: int) -> bool:
            if self.is_visible(hwnd):
                length = self.user32.GetWindowTextLengthW(hwnd)
                if length:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    if self.user32.GetWindowTextW(hwnd, buffer, len(buffer)):
                        result.append((int(hwnd), buffer.value))
            return True

        if not self.user32.EnumWindows(callback, 0):
            raise CaptureUnavailable("Impossible d'énumérer les fenêtres Windows.")
        return result

    def window_rect(self, hwnd: int) -> Rect:
        rectangle = wintypes.RECT()
        # DWM gives the visible frame rather than the invisible resize border.
        if not (self.dwm and self.dwm.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(rectangle), ctypes.sizeof(rectangle)) == 0):
            if not self.user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
                raise CaptureUnavailable("Impossible de lire les dimensions de la fenêtre.")
        return Rect(rectangle.left, rectangle.top,
                    rectangle.right - rectangle.left, rectangle.bottom - rectangle.top)

    def window_at(self, x: int, y: int) -> int:
        handle = self.user32.WindowFromPoint(wintypes.POINT(x, y))
        if not handle:
            return 0
        return int(self.user32.GetAncestor(handle, 2) or handle)  # GA_ROOT

    def owner(self, hwnd: int) -> int:
        return int(self.user32.GetWindow(hwnd, 4) or 0)  # GW_OWNER


class WindowsBackend:
    """Capture backend. Create and close an instance on its owning thread.

    A capture instance is lazy and bound to the first thread using MSS. Do not
    share a backend between the interface and the recording worker.
    """

    def __init__(self, *, win32: object | None = None,
                 mss_factory: Callable | None = None) -> None:
        self._native = win32
        self._mss_factory = mss_factory
        self._capture = None
        self._owner: int | None = None

    def _windows(self):
        if self._native is None:
            self._native = _NativeWindows()
        return self._native

    def _screen(self):
        current = threading.get_ident()
        if self._owner is not None and self._owner != current:
            raise CaptureUnavailable("Le moteur de capture appartient à un autre thread.")
        if self._capture is None:
            try:
                factory = self._mss_factory
                if factory is None:
                    import mss
                    factory = mss.mss
                self._capture = factory()
                self._owner = current
            except Exception as exc:
                raise CaptureUnavailable(f"Initialisation de la capture impossible : {exc}") from exc
        return self._capture

    def list_windows(self) -> list[CaptureTarget]:
        windows = self._windows().enumerate_windows()
        return sorted((CaptureTarget("window", title, hwnd=handle)
                       for handle, title in windows), key=lambda item: item.label.casefold())

    def list_monitors(self) -> list[CaptureTarget]:
        try:
            monitors = self._screen().monitors
            # Entry 0 is the virtual rectangle covering all physical screens.
            return [CaptureTarget("monitor", f"Moniteur {index} ({rect.width} × {rect.height})",
                                  rect=rect)
                    for index, monitor in enumerate(monitors[1:], start=1)
                    for rect in (Rect.from_any(monitor),)]
        except CaptureUnavailable:
            raise
        except Exception as exc:
            raise CaptureUnavailable(f"Énumération des moniteurs impossible : {exc}") from exc

    def resolve(self, target: CaptureTarget) -> Rect:
        if target.kind != "window":
            if target.rect is None:
                raise CaptureUnavailable("La zone de capture n'est pas définie.")
            return target.rect
        native = self._windows()
        if not native.is_window(target.hwnd):
            raise CaptureUnavailable("La fenêtre sélectionnée a été fermée.")
        if native.is_minimized(target.hwnd):
            raise CaptureUnavailable("La fenêtre sélectionnée est réduite. Restaurez-la.")
        if not native.is_visible(target.hwnd):
            raise CaptureUnavailable("La fenêtre sélectionnée n'est pas visible.")
        try:
            return native.window_rect(target.hwnd)
        except ValueError as exc:
            raise CaptureUnavailable("La fenêtre sélectionnée n'a pas de surface visible.") from exc

    def double_click_settings(self) -> tuple[float, int, int]:
        return double_click_settings()

    def mouse_button_down(self) -> bool:
        """Physical mouse button state for the hook watchdog; False if unknown."""
        query = getattr(self._windows(), "mouse_button_down", None)
        if query is None:
            return False
        try:
            return bool(query())
        except Exception:  # A failed probe must never stop a recording.
            return False

    def belongs_to_target(self, target: CaptureTarget, x: int, y: int) -> bool:
        return self.locate_click(target, x, y) is not None

    def locate_click(self, target: CaptureTarget, x: int, y: int) -> tuple[int, int] | None:
        """Return target-relative click coordinates, or None outside the target.

        Meant to run on the mouse-hook thread at the time of the click, before
        the application reacts to it. It only issues stateless Win32 queries
        and never touches MSS, so it does not conflict with the capture owner.
        """
        rectangle = self.resolve(target)
        if not rectangle.contains(x, y):
            return None
        if target.kind == "window" and not self._owned_by(self._windows().window_at(x, y),
                                                          target.hwnd):
            return None
        return x - rectangle.left, y - rectangle.top

    def _owned_by(self, hwnd: int, target_hwnd: int) -> bool:
        """True for the target itself or a window it owns, directly or not.

        GA_ROOT stops at owned top-level windows: without walking the owner
        chain, a click on the target's dialog or message box was dropped.
        """
        owner_of = getattr(self._windows(), "owner", None)
        for _ in range(16):  # Owner chains are short; never loop forever.
            if not hwnd:
                return False
            if hwnd == target_hwnd:
                return True
            if owner_of is None:
                return False
            hwnd = owner_of(hwnd)
        return False

    def grab(self, rectangle: Rect):
        """Capture only if the target intersects a physical monitor.

        Keep the requested rectangle when it is partly off screen so that
        click and mask coordinates remain target-relative. Pixels outside the
        desktop follow MSS/OS behavior (normally black on Windows). The virtual
        desktop bounding rectangle is insufficient: gaps between screens must
        not count as visible monitor content.
        """
        from PIL import Image
        try:
            screen = self._screen()
            physical_monitors = [Rect.from_any(monitor) for monitor in screen.monitors[1:]]
            if not any(rectangle.left < monitor.right and rectangle.right > monitor.left
                       and rectangle.top < monitor.bottom and rectangle.bottom > monitor.top
                       for monitor in physical_monitors):
                raise CaptureUnavailable(
                    "La zone sélectionnée est entièrement hors des écrans visibles. "
                    "Replacez la fenêtre ou la zone sur un moniteur."
                )
            screenshot = screen.grab(rectangle.as_dict())
            # Decode MSS' native BGRA buffer in one C pass; its ``rgb``
            # property first repacks every pixel (about 70 ms more in 4K).
            return Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        except CaptureUnavailable:
            raise
        except Exception as exc:
            raise CaptureUnavailable(f"Capture de l'image impossible : {exc}") from exc

    def close(self) -> None:
        if self._capture is not None:
            if self._owner != threading.get_ident():
                raise CaptureUnavailable("La capture doit être fermée par son thread propriétaire.")
            self._capture.close()
            self._capture = None
            self._owner = None
