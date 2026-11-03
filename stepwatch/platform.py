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

    def belongs_to_target(self, target: CaptureTarget, x: int, y: int) -> bool:
        rectangle = self.resolve(target)
        if not rectangle.contains(x, y):
            return False
        return target.kind != "window" or self._windows().window_at(x, y) == target.hwnd

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
            return Image.frombytes("RGB", screenshot.size, screenshot.rgb)
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
