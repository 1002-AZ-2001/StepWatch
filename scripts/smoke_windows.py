"""Opt-in native Windows and packaged-application smoke checks.

Run from a checkout after installing ``requirements-dev.txt``:
    python scripts/smoke_windows.py
    python scripts/smoke_windows.py --executable dist/StepWatch/StepWatch.exe

The default check opens its own Tk target, captures only that target into a
temporary directory, and starts/stops the real Raw Input mouse listener. It
sends one left click (SendInput) inside its own target window to check that
Raw Input reports it as a step; it never generates keyboard input. --executable runs only the packaged
UI startup/close check; it never starts recording or repeats the native check.
An interactive Windows desktop is required. Failures are never silently skipped.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def pump_until(root, predicate, message: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(.01)
    raise RuntimeError(message)


def check_color(image, expected: tuple[int, int, int]) -> None:
    actual = image.getpixel((image.width // 2, image.height // 2))
    require(max(abs(left - right) for left, right in zip(actual, expected)) <= 3,
            f"Native capture pixels differ: expected {expected}, got {actual}")


def has_color(image, expected: tuple[int, int, int]) -> bool:
    actual = image.getpixel((image.width // 2, image.height // 2))
    return max(abs(left - right) for left, right in zip(actual, expected)) <= 3


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _Input(ctypes.Structure):
    class _Union(ctypes.Union):
        _fields_ = [("mi", _MouseInput), ("padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _Union)]


def click_left(x: int, y: int) -> None:
    """Move the cursor to (x, y) and send one left click, like a user would."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    require(bool(user32.SetCursorPos(x, y)), "SetCursorPos failed")
    time.sleep(.1)
    events = (_Input * 2)()
    for event, flag in zip(events, (0x0002, 0x0004)):  # MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        event.type = 0  # INPUT_MOUSE
        event.mi.dwFlags = flag
    sent = user32.SendInput(2, events, ctypes.sizeof(_Input))
    require(sent == 2, f"SendInput sent {sent} of 2 events (error {ctypes.get_last_error()})")


def native_smoke() -> None:
    import tkinter as tk
    from PIL import Image
    from stepwatch.rawinput import RawMouseListener

    from stepwatch.engine import Recorder
    from stepwatch.models import CaptureConfig, Rect
    from stepwatch.platform import CaptureUnavailable, WindowsBackend
    from stepwatch.storage import SessionStore

    root = tk.Tk()
    backend = WindowsBackend()
    recorder = None
    listeners = []
    title = f"StepWatch native smoke {os.getpid()}"
    try:
        root.title(title)
        root.configure(bg="#123456")
        root.geometry("520x360+100+100")
        root.attributes("-topmost", True)
        root.lift()
        root.update()
        selected = []

        def find_target():
            selected[:] = [target for target in backend.list_windows() if target.label == title]
            return len(selected) == 1

        pump_until(root, find_target, "The native Tk target was not enumerated")
        target = selected[0]
        monitors = backend.list_monitors()
        require(bool(monitors), "MSS did not report a physical monitor")
        # Single instance: a second named mutex in the session must be refused.
        from stepwatch.instance import InstanceLock
        first, second = InstanceLock(), InstanceLock()
        require(first.acquire(), "The single-instance mutex could not be created")
        require(not second.acquire(), "A second StepWatch instance was not detected")
        first.release()
        # Global recording shortcuts: real RegisterHotKey/GetMessage thread.
        from stepwatch.hotkeys import GlobalHotkeys
        hotkeys = GlobalHotkeys()
        require(hotkeys.start(), "The global hot key thread did not start")
        hotkeys.stop()
        require(hotkeys._thread is None, "The global hot key thread did not stop")
        # Hook watchdog probe: the real GetAsyncKeyState binding must answer.
        require(isinstance(backend._windows().mouse_button_down(), bool),
                "GetAsyncKeyState did not report the mouse button state")
        initial = backend.resolve(target)
        # DWM composition can finish after Tk's update() returns. Wait for
        # actual painted pixels, rather than relying on a fixed startup sleep.
        pump_until(root, lambda: has_color(backend.grab(backend.resolve(target)), (18, 52, 86)),
                   "The target's expected native pixels did not appear")

        root.geometry("640x420+160+140")
        pump_until(root, lambda: backend.resolve(target) != initial,
                   "Window movement/resize was not reflected by Win32")
        moved = backend.resolve(target)
        require(moved.width > initial.width and moved.height > initial.height,
                "Native window rectangle did not grow after resize")
        pump_until(root, lambda: has_color(backend.grab(backend.resolve(target)), (18, 52, 86)),
                   "The resized target's expected native pixels did not appear")

        root.iconify()
        pump_until(root, lambda: root.state() == "iconic", "Tk target did not minimize")
        try:
            backend.resolve(target)
        except CaptureUnavailable:
            pass
        else:
            raise RuntimeError("A minimized window was incorrectly available for capture")
        root.deiconify()
        root.lift()

        def restored():
            try:
                backend.resolve(target)
                return True
            except CaptureUnavailable:
                return False

        pump_until(root, restored, "The restored target remained unavailable")
        pump_until(root, lambda: has_color(backend.grab(backend.resolve(target)), (18, 52, 86)),
                   "The restored target was not visibly repainted")

        def listener_factory(callback):
            listener = RawMouseListener(callback)
            listeners.append(listener)
            return listener

        temporary = tempfile.TemporaryDirectory(prefix="stepwatch-native-")
        try:
            store = SessionStore(Path(temporary.name), "Native Windows smoke")
            recorder = Recorder(
                target, CaptureConfig(mode="hybrid", interval=.15,
                                      masks=(Rect(20, 60, 25, 25),)),
                store, listener_factory=listener_factory,
            )
            recorder.start()
            pump_until(root, lambda: bool(store.steps),
                       "The native Recorder did not save an initial capture")
            require(len(listeners) == 1 and listeners[0].is_alive(),
                    "The Raw Input mouse listener did not start")
            pump_until(root, lambda: listeners[0].wait(0), "The Raw Input listener never became ready", timeout=5)
            require(listeners[0].error is None, f"Raw Input registration failed: {listeners[0].error}")

            # One real left click inside the target (injected input reaches Raw Input).
            rect = backend.resolve(target)
            click_left(rect.left + rect.width // 2, rect.top + rect.height // 2)
            pump_until(root, lambda: any(step["trigger"] == "local_click" for step in store.steps),
                       "A real left click was not reported by Raw Input", timeout=10)

            root.configure(bg="#bc783a")
            pump_until(root, lambda: any(step["trigger"] == "visual_change" for step in store.steps),
                       "A real window color change was not recorded")
            require(recorder.stop(timeout=10), "The native Recorder did not stop")
            require(recorder.state == "stopped", f"Recorder ended in {recorder.state!r}")
            require(not listeners[0].is_alive(), "The Raw Input listener remained alive after stop")
            require(not recorder._thread.is_alive(), "The capture thread remained alive after stop")
            metadata = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
            require(metadata["steps"][0]["trigger"] == "initial", "Initial step is missing")
            visual = next(step for step in metadata["steps"] if step["trigger"] == "visual_change")
            for step, color in ((metadata["steps"][0], (18, 52, 86)), (visual, (188, 120, 58))):
                with Image.open(store.directory / step["image"]) as image:
                    check_color(image, color)
                    require(image.getpixel((25, 65)) == (0, 0, 0), "Sensitive mask was not applied")
            require((store.directory / "rapport.html").is_file(), "Offline report was not written")
            pngs_before = sorted((store.directory / "captures").glob("*.png"))
            count_before = len(store.steps)
            root.configure(bg="#ffffff")
            deadline = time.monotonic() + .4  # More than two sampling intervals.
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            require(len(store.steps) == count_before
                    and sorted((store.directory / "captures").glob("*.png")) == pngs_before,
                    "An image was saved after Recorder.stop() returned")
        finally:
            # Windows cannot delete open PNG/report files. Stop the worker
            # before TemporaryDirectory cleanup even when an assertion fails.
            if recorder is not None:
                require(recorder.stop(timeout=10), "Native capture thread could not be released")
            temporary.cleanup()

        root.destroy()
        try:
            backend.resolve(target)
        except CaptureUnavailable:
            pass
        else:
            raise RuntimeError("A closed window was incorrectly available for capture")
        print(json.dumps({"check": "native_windows", "status": "passed",
                          "monitors": len(monitors), "captures": count_before,
                          "mouse_input_generated": True}), flush=True)
    finally:
        if recorder is not None:
            require(recorder.stop(timeout=10), "Native capture thread could not be released")
        backend.close()
        try:
            root.destroy()
        except tk.TclError:
            pass


def packaged_smoke(executable: Path) -> None:
    from stepwatch.platform import WindowsBackend

    executable = executable.resolve(strict=True)
    require(executable.is_file(), f"Executable does not exist: {executable}")
    backend = WindowsBackend()
    expected_title = "StepWatch — Documentation d’intervention"
    previous_handles = {target.hwnd for target in backend.list_windows()}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    process = None
    handle = None
    temporary = tempfile.TemporaryDirectory(prefix="stepwatch-packaged-")
    try:
        environment = os.environ.copy()
        # Keep the packaged diagnostic journal isolated from the runner's
        # other jobs. No credentials are inspected, logged or changed.
        environment["LOCALAPPDATA"] = str(Path(temporary.name) / "local")
        process = subprocess.Popen([str(executable)], cwd=temporary.name, env=environment)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            require(process.poll() is None,
                    f"Packaged application exited before showing its UI (code {process.returncode})")
            matches = [target for target in backend.list_windows()
                       if target.label == expected_title and target.hwnd not in previous_handles]
            if matches:
                require(len(matches) == 1, "Several new packaged StepWatch windows appeared")
                handle = matches[0].hwnd
                break
            time.sleep(.1)
        require(handle is not None, "Packaged StepWatch did not show its main window within 60 seconds")
        # A onefile build uses a parent bootloader and a child process for
        # the UI. Find its new HWND by title instead of assuming parent PID.
        require(bool(user32.PostMessageW(handle, 0x0010, 0, 0)), "WM_CLOSE could not be posted")
        returncode = process.wait(timeout=20)
        require(returncode == 0, f"Packaged application failed on close (code {returncode})")
        require(not any(target.hwnd == handle for target in backend.list_windows()),
                "The packaged main window remained after the process exited")
        print(json.dumps({"check": "packaged_windows", "status": "passed",
                          "executable": executable.name, "recording_started": False}), flush=True)
    finally:
        if handle is not None:
            user32.PostMessageW(handle, 0x0010, 0, 0)
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Terminate only the process tree started by this failed check.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               check=False, capture_output=True)
                process.wait(timeout=5)
        backend.close()
        temporary.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path,
                        help="Run only the packaged UI startup/close check for this .exe")
    arguments = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This smoke check requires an interactive Windows desktop")
    # Script execution puts scripts/, not the checkout root, on sys.path.
    # Exercise this checkout without requiring an editable package install.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stepwatch.platform import enable_dpi_awareness
    enable_dpi_awareness()  # Before creating any window (Tk test target) or hook.
    if arguments.executable is None:
        native_smoke()
    else:
        packaged_smoke(arguments.executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
