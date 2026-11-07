"""Local diagnostic log: messages and tracebacks, never images or keystrokes.

The Windows build is windowed, so ``sys.stderr`` is ``None``: without this
module, exceptions in Tk callbacks, threads or the mouse hook leave no trace.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
from pathlib import Path
import platform as _platform
import sys
import threading

from . import __version__

LOG_NAME = "stepwatch.log"
_state: dict = {}


def log_directory() -> Path:
    """%LOCALAPPDATA%\\StepWatch\\logs on Windows, XDG state directory elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "StepWatch" / "logs"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "stepwatch"


def setup(directory: Path | None = None) -> Path | None:
    """Start the rotating log (4 x 1 MB) and the crash hooks; None if impossible.

    Calling it again first undoes the previous setup, so lines are never
    duplicated. Logging must never prevent StepWatch from starting.
    """
    reset()
    directory = Path(directory) if directory is not None else log_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / LOG_NAME, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(name)s : %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    previous_excepthook = sys.excepthook
    previous_thread_hook = threading.excepthook
    _state.update(handler=handler, root_level=root.level,
                  app_level=logging.getLogger("stepwatch").level,
                  excepthook=previous_excepthook, thread_hook=previous_thread_hook)
    # Libraries (pynput reports hook callback errors) at WARNING, StepWatch at INFO.
    root.setLevel(logging.WARNING)
    logging.getLogger("stepwatch").setLevel(logging.INFO)

    def excepthook(kind, value, traceback):
        logging.getLogger("stepwatch").critical("Exception non gérée", exc_info=(kind, value, traceback))
        # An exception callback can already be in flight while reset() removes
        # this installation, or a new setup() replaces it. Keep its original
        # chain independent of the mutable global installation state.
        previous_excepthook(kind, value, traceback)

    def thread_hook(arguments):
        name = arguments.thread.name if arguments.thread is not None else "?"
        logging.getLogger("stepwatch").critical(
            "Exception non gérée dans le thread %s", name,
            exc_info=(arguments.exc_type, arguments.exc_value, arguments.exc_traceback))
        previous_thread_hook(arguments)

    _state.update(installed_excepthook=excepthook, installed_thread_hook=thread_hook)
    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    if not faulthandler.is_enabled():
        # Hard crashes (access violation in a native call) bypass Python.
        crash_file = None
        try:
            crash_file = open(directory / "crash.log", "a", encoding="utf-8")
            faulthandler.enable(file=crash_file, all_threads=True)
            _state["crash_file"] = crash_file
        except (OSError, ValueError, RuntimeError):
            if crash_file is not None:
                crash_file.close()
    logging.getLogger("stepwatch").info(
        "StepWatch %s démarré (Python %s, %s).", __version__,
        _platform.python_version(), _platform.platform())
    return directory / LOG_NAME


def reset() -> None:
    """Release our files and hooks, preserving hooks installed later by others."""
    state = _state.copy()
    _state.clear()
    handler = state.get("handler")
    if handler is None:
        return
    root = logging.getLogger()
    root.removeHandler(handler)
    try:
        handler.close()
    except OSError:
        pass  # A full/unavailable logging volume must not prevent cleanup.
    finally:
        root.setLevel(state["root_level"])
        logging.getLogger("stepwatch").setLevel(state["app_level"])
        if sys.excepthook is state["installed_excepthook"]:
            sys.excepthook = state["excepthook"]
        if threading.excepthook is state["installed_thread_hook"]:
            threading.excepthook = state["thread_hook"]
        crash_file = state.get("crash_file")
        if crash_file is not None:
            try:
                faulthandler.disable()
            finally:
                crash_file.close()
