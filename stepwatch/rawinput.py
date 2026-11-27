"""Clics de souris sous Windows par Raw Input, sans hook système.

Les versions précédentes utilisaient un hook bas niveau (``WH_MOUSE_LL``,
bibliothèque pynput, qui embarque aussi l'écoute du clavier). Ce mécanisme est
celui des logiciels espions et pèse dans les heuristiques des antivirus ; Windows
le retire aussi silencieusement quand le rappel dépasse ``LowLevelHooksTimeout``.

Raw Input (``RegisterRawInputDevices`` avec ``RIDEV_INPUTSINK``) est l'API
documentée par Microsoft pour lire un périphérique d'entrée, y compris quand une
autre application a le focus. StepWatch ne s'abonne qu'à la souris (page 0x01,
usage 0x02) : aucune frappe clavier n'est reçue. Les messages ``WM_INPUT``
arrivent dans une fenêtre invisible réservée aux messages ; la position est lue
par ``GetCursorPos`` (pixels physiques avec la gestion DPI par moniteur).

Même interface que l'écouteur pynput utilisé auparavant : ``start``, ``stop``,
``join``, ``is_alive`` et un rappel ``callback(x, y, bouton, appuyé)`` où le
bouton vaut ``"Button.left"``, ``"Button.right"``…
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

LOGGER = logging.getLogger(__name__)

WM_INPUT = 0x00FF
WM_QUIT = 0x0012
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
HWND_MESSAGE = wintypes.HWND(-3)
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02

#: (indicateur RI_MOUSE_*, bouton, appuyé)
BUTTON_FLAGS = (
    (0x0001, "Button.left", True), (0x0002, "Button.left", False),
    (0x0004, "Button.right", True), (0x0008, "Button.right", False),
    (0x0010, "Button.middle", True), (0x0020, "Button.middle", False),
    (0x0040, "Button.x1", True), (0x0080, "Button.x1", False),
    (0x0100, "Button.x2", True), (0x0200, "Button.x2", False),
)


def _last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)  # Windows uniquement.
    return getter() if getter is not None else 0


def button_events(flags: int) -> list[tuple[str, bool]]:
    """Boutons appuyés ou relâchés dans un message Raw Input, dans l'ordre des indicateurs."""
    return [(button, pressed) for flag, button, pressed in BUTTON_FLAGS if flags & flag]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class _ButtonsStruct(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _ButtonsUnion(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("buttons", _ButtonsStruct)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _ButtonsUnion), ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class _User32:
    """Fonctions Win32 typées ; toutes appelées sur le thread de l'écouteur."""

    def __init__(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateWindowExW": (wintypes.HWND, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]),
            "DestroyWindow": (wintypes.BOOL, [wintypes.HWND]),
            "RegisterRawInputDevices": (wintypes.BOOL, [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]),
            "GetRawInputData": (wintypes.UINT, [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                                ctypes.POINTER(wintypes.UINT), wintypes.UINT]),
            "GetMessageW": (wintypes.BOOL, [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]),
            "TranslateMessage": (wintypes.BOOL, [ctypes.POINTER(wintypes.MSG)]),
            "DispatchMessageW": (ctypes.c_ssize_t, [ctypes.POINTER(wintypes.MSG)]),
            "PostThreadMessageW": (wintypes.BOOL, [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
            "GetCursorPos": (wintypes.BOOL, [ctypes.POINTER(wintypes.POINT)]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(user32, name)
            function.restype, function.argtypes = restype, argtypes
            setattr(self, name, function)
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.GetCurrentThreadId = kernel32.GetCurrentThreadId


class RawMouseListener(threading.Thread):
    """Fil d'écoute des boutons de la souris ; ``stop()`` le termine proprement."""

    def __init__(self, callback: Callable[[int, int, str, bool], None], api_factory=None, post_quit=None):
        super().__init__(name="StepWatch-souris", daemon=True)
        self._callback = callback
        self._api_factory = api_factory or _User32
        self._post_quit = post_quit or _post_quit
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self.error: str | None = None

    def wait(self, timeout: float | None = None) -> bool:
        """Vrai quand la souris est enregistrée auprès de Windows."""
        return self._ready.wait(timeout)

    def run(self) -> None:
        api = None
        window = None
        try:
            api = self._api_factory()
            self._thread_id = api.GetCurrentThreadId()
            # Fenêtre invisible réservée aux messages : classe prédéfinie, aucune procédure à écrire.
            window = api.CreateWindowExW(0, "STATIC", "StepWatch Raw Input", 0, 0, 0, 0, 0,
                                         HWND_MESSAGE, None, None, None)
            if not window:
                raise OSError(_last_error(), "CreateWindowExW")
            device = RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, RIDEV_INPUTSINK, window)
            if not api.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                raise OSError(_last_error(), "RegisterRawInputDevices")
            self._ready.set()
            if self._stopping.is_set():
                return
            message = wintypes.MSG()
            while api.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_INPUT:
                    self._handle(api, message.lParam)
                api.TranslateMessage(ctypes.byref(message))
                api.DispatchMessageW(ctypes.byref(message))  # Libère les données WM_INPUT.
        except Exception as exc:
            self.error = str(exc)
            LOGGER.exception("Écoute Raw Input de la souris interrompue")
        finally:
            self._ready.set()
            if api is not None and window:
                removal = RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, RIDEV_REMOVE, None)
                api.RegisterRawInputDevices(ctypes.byref(removal), 1, ctypes.sizeof(RAWINPUTDEVICE))
                api.DestroyWindow(window)

    def _handle(self, api, handle) -> None:
        data = RAWINPUT()
        size = wintypes.UINT(ctypes.sizeof(data))
        read = api.GetRawInputData(handle, RID_INPUT, ctypes.byref(data), ctypes.byref(size),
                                   ctypes.sizeof(RAWINPUTHEADER))
        if read in (0, 0xFFFFFFFF) or data.header.dwType != RIM_TYPEMOUSE:
            return
        events = button_events(data.mouse.u.buttons.usButtonFlags)
        if not events:
            return  # Simple déplacement : la position ne sert qu'aux clics.
        point = wintypes.POINT()
        if not api.GetCursorPos(ctypes.byref(point)):
            return
        for button, pressed in events:
            try:
                self._callback(point.x, point.y, button, pressed)
            except Exception:
                # Une erreur de traitement d'un clic ne doit pas arrêter l'écoute.
                LOGGER.exception("Traitement d'un clic impossible")

    def stop(self) -> None:
        """Termine la boucle de messages ; sans effet si le fil n'a pas démarré."""
        self._stopping.set()
        if self._thread_id is not None and self.is_alive():
            self._post_quit(self._thread_id)


def _post_quit(thread_id: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
