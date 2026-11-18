"""Wayland capture from a user-authorized desktop portal stream."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time

from PIL import Image

from .models import CaptureTarget, Rect
from .platform import CaptureCancelled, CaptureEnded, CaptureUnavailable


_HELPER = Path(__file__).with_name("_wayland_portal.py")
_SYSTEM_PYTHON = Path("/usr/bin/python3")
# Reply: type, width, height, row stride, payload length (network byte order).
_HEADER = struct.Struct("!cIIII")
_MAX_FRAME_BYTES = 256 * 1024 * 1024
_MAX_MESSAGE_BYTES = 4096


def _system_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        # Do not load bundled libraries into the distro Python or GStreamer.
        previous = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if previous is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = previous
    return environment


class WaylandBackend:
    """Own a private connection to the system-Python portal helper.

    The recording thread owns all methods. The helper runs outside the app's
    bundled or virtual Python environment to match the distro's PyGObject ABI.
    """

    uses_portal = True

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._socket: socket.socket | None = None
        self._stop: threading.Event | None = None
        self._rect: Rect | None = None

    def prepare(self, target: CaptureTarget, *, stop_event: threading.Event) -> None:
        if target.kind != "portal":
            raise CaptureUnavailable("Choisissez la source de capture dans le portail Wayland.")
        if not _SYSTEM_PYTHON.is_file() or not _HELPER.is_file():
            raise CaptureUnavailable("La capture Wayland nécessite Python système (python3) et l’assistant StepWatch.")
        self._stop = stop_event
        self._check_cancelled()
        parent, child = socket.socketpair()
        self._socket = parent
        parent.settimeout(0.15)
        try:
            try:
                self._process = subprocess.Popen(
                    [str(_SYSTEM_PYTHON), "-I", str(_HELPER), str(child.fileno())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    env=_system_environment(), pass_fds=(child.fileno(),),
                )
            finally:
                child.close()
            kind, width, height, stride, length = self._read_header(time.monotonic() + 150)
            if kind != b"R" or stride or length or not (0 < width * height * 4 <= _MAX_FRAME_BYTES):
                raise CaptureEnded("L’assistant Wayland a renvoyé une résolution invalide.")
            self._rect = Rect(0, 0, width, height)
        except BaseException:
            self.close()
            raise

    def resolve(self, target: CaptureTarget) -> Rect:
        if target.kind != "portal" or self._rect is None:
            raise CaptureUnavailable("Aucun flux Wayland n’a été autorisé.")
        return self._rect

    def grab(self, _rectangle: Rect) -> Image.Image | None:
        self._check_cancelled()
        if self._socket is None:
            raise CaptureEnded("Le flux Wayland a été fermé.")
        try:
            self._socket.sendall(b"G")
        except OSError as exc:
            self.check_live()
            raise CaptureEnded("Impossible de lire le flux Wayland.") from exc
        deadline = time.monotonic() + 5
        kind, width, height, stride, length = self._read_header(deadline)
        if kind == b"I" and not (width or height or stride or length):
            return None  # The compositor hasn't supplied another frame yet.
        if (kind not in (b"F", b"X") or not width or not height or stride < width * 4
                or length != stride * height or length > _MAX_FRAME_BYTES):
            raise CaptureEnded("L’assistant Wayland a renvoyé une image invalide.")
        pixels = self._read_exact(length, deadline)
        image = Image.frombytes("RGB", (width, height), pixels, "raw", "BGRX" if kind == b"F" else "RGBX", stride)
        self._rect = Rect(0, 0, width, height)
        return image

    def check_live(self) -> None:
        """Detect sharing revoked at the desktop, including while paused."""
        self._check_cancelled()
        if self._process is not None and self._process.poll() is not None:
            # An orderly helper shutdown sends the reason before exiting.
            self._read_header(time.monotonic() + 0.15)
            raise CaptureEnded("Le flux Wayland a été fermé par le bureau.")

    def _check_cancelled(self) -> None:
        if self._stop is not None and self._stop.is_set():
            raise CaptureCancelled("Partage Wayland arrêté.")

    def _read_exact(self, length: int, deadline: float) -> bytearray:
        if self._socket is None:
            raise CaptureEnded("Le flux Wayland a été fermé.")
        data = bytearray(length)
        with memoryview(data) as view:
            offset = 0
            while offset < length:
                self._check_cancelled()
                if time.monotonic() >= deadline:
                    raise CaptureEnded("Le portail ou le flux Wayland ne répond plus.")
                try:
                    received = self._socket.recv_into(view[offset:])
                except TimeoutError:
                    continue
                except OSError as exc:
                    raise CaptureEnded("Le flux Wayland a été interrompu.") from exc
                if not received:
                    raise CaptureEnded("Le flux Wayland a été fermé par le bureau.")
                offset += received
        return data

    def _read_header(self, deadline: float) -> tuple[bytes, int, int, int, int]:
        header = _HEADER.unpack(self._read_exact(_HEADER.size, deadline))
        kind, _, _, _, length = header
        if kind in (b"C", b"E"):
            if length > _MAX_MESSAGE_BYTES:
                raise CaptureEnded("L’assistant Wayland a renvoyé un message invalide.")
            reason = self._read_exact(length, deadline).decode("utf-8", errors="replace")
            if kind == b"C":
                raise CaptureCancelled(reason)
            raise CaptureEnded(reason)
        return header

    def close(self) -> None:
        self._rect = None
        if self._socket is not None:
            self._socket.close()  # Wakes the helper even in the consent dialog.
            self._socket = None
        process = self._process
        if process is None:
            return
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
        self._process = None
