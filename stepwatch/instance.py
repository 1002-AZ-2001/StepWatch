"""One StepWatch window per user session.

Two instances would install two mouse hooks and write two reports for the
same clicks. Windows uses a named mutex in the session namespace; Linux an
advisory ``flock`` on a file of the runtime directory. Both are released by
the system when the process ends, even after a crash.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MUTEX_NAME = "Local\\StepWatch-instance-unique"
ERROR_ALREADY_EXISTS = 183


def lock_path(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_RUNTIME_DIR") or environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "stepwatch.lock"


class InstanceLock:
    def __init__(self, path: Path | None = None, platform: str | None = None):
        self.platform = sys.platform if platform is None else platform
        self.path = Path(path) if path is not None else None
        self._handle = None

    def acquire(self, wait: float = 0) -> bool:
        """True when this process is the only StepWatch; never raises.

        ``wait`` > 0 retries while another instance closes (restart after an update).
        """
        deadline = time.monotonic() + max(0, wait)
        while not self._try_acquire():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)
        return True

    def _try_acquire(self) -> bool:
        try:
            if self.platform == "win32":
                return self._acquire_windows()
            return self._acquire_posix()
        except OSError:
            return True  # Unable to check: do not block the technician.

    def _acquire_windows(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle  # Kept open for the life of the process.
        return True

    def _acquire_posix(self) -> bool:
        import fcntl

        path = self.path or lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        if self.platform == "win32":
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(handle)
        else:
            handle.close()
