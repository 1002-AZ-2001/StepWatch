"""Launch the installed Linux GUI, request WM_DELETE_WINDOW, require exit 0.

Uses Xlib directly, so the check needs only a dedicated Xorg/Xvfb display and
does not require a window manager or additional automation packages.
"""

import argparse
import ctypes as C
from ctypes.util import find_library
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


class MessageData(C.Union):
    _fields_ = [("b", C.c_char * 20), ("s", C.c_short * 10), ("l", C.c_long * 5)]


class ClientMessage(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), ("window", C.c_ulong), ("message_type", C.c_ulong),
                ("format", C.c_int), ("data", MessageData)]


class XEvent(C.Union):
    _fields_ = [("client", ClientMessage), ("padding", C.c_long * 24)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    library = find_library("X11")
    if not library or not os.environ.get("DISPLAY"):
        raise RuntimeError("Un affichage X11 de test et libX11 sont requis.")
    x11 = C.CDLL(library)
    signatures = {
        "XOpenDisplay": ([C.c_char_p], C.c_void_p),
        "XDefaultRootWindow": ([C.c_void_p], C.c_ulong),
        "XQueryTree": ([C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                        C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)], C.c_int),
        "XFetchName": ([C.c_void_p, C.c_ulong, C.POINTER(C.c_void_p)], C.c_int),
        "XFree": ([C.c_void_p], C.c_int),
        "XInternAtom": ([C.c_void_p, C.c_char_p, C.c_int], C.c_ulong),
        "XSendEvent": ([C.c_void_p, C.c_ulong, C.c_int, C.c_long, C.POINTER(XEvent)], C.c_int),
        "XFlush": ([C.c_void_p], C.c_int),
        "XCloseDisplay": ([C.c_void_p], C.c_int),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(x11, name)
        function.argtypes, function.restype = arguments, result
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Impossible d’ouvrir l’affichage de test.")

    def windows(parent, depth=0):
        result = set()
        name = C.c_void_p()
        if x11.XFetchName(display, parent, C.byref(name)) and name.value:
            try:
                if C.string_at(name).startswith(b"StepWatch"):
                    result.add(parent)
            finally:
                x11.XFree(name)
        if depth >= 5:
            return result
        root, owner, count = C.c_ulong(), C.c_ulong(), C.c_uint()
        children = C.POINTER(C.c_ulong)()
        if x11.XQueryTree(display, parent, C.byref(root), C.byref(owner), C.byref(children), C.byref(count)):
            try:
                for index in range(count.value):
                    result.update(windows(children[index], depth + 1))
            finally:
                if children:
                    x11.XFree(children)
        return result

    process = None
    try:
        root = x11.XDefaultRootWindow(display)
        existing = windows(root)
        with tempfile.TemporaryDirectory(prefix="stepwatch-package-smoke-") as temporary:
            environment = dict(os.environ, XDG_STATE_HOME=temporary)
            log_path = Path(temporary) / "launch.log"
            with log_path.open("wb") as log:
                process = subprocess.Popen([str(args.executable.resolve())], env=environment,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 60
                candidates = set()
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Le paquet s'est arrêté avant ouverture : {log_path.read_text(errors='replace')}")
                    candidates = windows(root) - existing
                    if candidates:
                        break
                    time.sleep(.1)
                if not candidates:
                    raise RuntimeError("La fenêtre du paquet ne s'est pas ouverte en 60 secondes.")
                event = XEvent()
                event.client.type = 33  # ClientMessage
                event.client.display = display
                event.client.window = min(candidates)
                event.client.message_type = x11.XInternAtom(display, b"WM_PROTOCOLS", False)
                event.client.format = 32
                event.client.data.l[0] = x11.XInternAtom(display, b"WM_DELETE_WINDOW", False)
                if not x11.XSendEvent(display, event.client.window, False, 0, C.byref(event)):
                    raise RuntimeError("Impossible de demander la fermeture du paquet.")
                x11.XFlush(display)
                if process.wait(timeout=20) != 0:
                    raise RuntimeError(f"Échec du paquet : {log_path.read_text(errors='replace')}")
        print("Paquet Linux : fenêtre ouverte et fermée proprement, code de sortie 0.")
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        x11.XCloseDisplay(display)


if __name__ == "__main__":
    main()
