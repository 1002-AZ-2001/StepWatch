"""System-wide shortcuts while recording (Windows): pause, note, stop.

RegisterHotKey only reports the registered key combinations: no other key
is observed, so StepWatch still records no keystroke. The hot keys belong to
a dedicated thread with its own message loop (WM_HOTKEY is posted to the
registering thread). Actions are handed to the interface through a queue.

Combinations: Ctrl+Maj+F9 pause/resume, Ctrl+Maj+F10 note, Ctrl+Maj+F11
stop. F12 is reserved by Windows for the debugger.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from ctypes import wintypes

LOGGER = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
VK_F9, VK_F10, VK_F11 = 0x78, 0x79, 0x7A
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

#: Action name, modifiers, virtual key, label shown to the user.
SHORTCUTS = (
    ("pause", MOD_CONTROL | MOD_SHIFT, VK_F9, "Ctrl+Maj+F9"),
    ("note", MOD_CONTROL | MOD_SHIFT, VK_F10, "Ctrl+Maj+F10"),
    ("stop", MOD_CONTROL | MOD_SHIFT, VK_F11, "Ctrl+Maj+F11"),
)
LABELS = {action: label for action, _, _, label in SHORTCUTS}
#: Same combinations as Tk sequences, for the StepWatch windows themselves.
TK_SEQUENCES = {"pause": "<Control-Shift-F9>", "note": "<Control-Shift-F10>", "stop": "<Control-Shift-F11>"}


class Win32HotkeyApi:
    """Thin ctypes wrapper; every call happens on the hot key thread."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def thread_id(self) -> int:
        return self.kernel32.GetCurrentThreadId()

    def register(self, identifier: int, modifiers: int, key: int) -> bool:
        return bool(self.user32.RegisterHotKey(None, identifier, modifiers | MOD_NOREPEAT, key))

    def unregister(self, identifier: int) -> None:
        self.user32.UnregisterHotKey(None, identifier)

    def next_hotkey(self) -> int | None:
        """Block until WM_HOTKEY (its identifier) or WM_QUIT (None)."""
        message = wintypes.MSG()
        while True:
            result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result in (0, -1):
                return None
            if message.message == WM_HOTKEY:
                return int(message.wParam)

    def post_quit(self, thread_id: int) -> None:
        self.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)


class GlobalHotkeys:
    """Start while recording, stop afterwards. ``events`` receives action names."""

    def __init__(self, api_factory=None):
        self.events: queue.Queue = queue.Queue()
        self.failed: list[str] = []  # Labels already used by another application.
        self._api_factory = api_factory or Win32HotkeyApi
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._api = None
        self._ready = threading.Event()

    @staticmethod
    def supported(platform: str | None = None) -> bool:
        return (sys.platform if platform is None else platform) == "win32"

    def start(self) -> bool:
        if self._thread is not None:
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="StepWatch-raccourcis", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self._thread_id is not None

    def _run(self):
        registered = []
        try:
            self._api = self._api_factory()
            self._thread_id = self._api.thread_id()
            for identifier, (action, modifiers, key, label) in enumerate(SHORTCUTS, start=1):
                if self._api.register(identifier, modifiers, key):
                    registered.append(identifier)
                else:
                    self.failed.append(label)
            self._ready.set()
            while True:
                identifier = self._api.next_hotkey()
                if identifier is None:
                    break
                if 1 <= identifier <= len(SHORTCUTS):
                    self.events.put(SHORTCUTS[identifier - 1][0])
        except Exception:
            LOGGER.exception("Raccourcis globaux indisponibles")
        finally:
            self._ready.set()
            for identifier in registered:
                try:
                    self._api.unregister(identifier)
                except Exception:
                    LOGGER.exception("Libération du raccourci %s impossible", identifier)

    def stop(self, timeout: float = 2) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        if self._thread_id is not None and self._api is not None:
            self._api.post_quit(self._thread_id)
        thread.join(timeout)
        self._thread_id = None
