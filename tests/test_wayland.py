"""Private Wayland transport tests use a real child without a live desktop."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

from stepwatch.models import CaptureTarget, Rect
from stepwatch.platform import CaptureCancelled, CaptureEnded
from stepwatch.wayland import WaylandBackend, _system_environment


_HELPER_CODE = """
import socket
import struct
import sys

peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0, fileno=int(sys.argv[1]))
header = struct.Struct('!cIIII')

def send(kind, width=0, height=0, stride=0, payload=b''):
    peer.sendall(header.pack(kind, width, height, stride, len(payload)))
    peer.sendall(payload)
"""


@unittest.skipUnless(os.name == "posix", "Unix descriptor passing required")
class WaylandTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.helper = Path(temporary.name) / "helper.py"
        helper_path = patch("stepwatch.wayland._HELPER", self.helper)
        python_path = patch("stepwatch.wayland._SYSTEM_PYTHON", Path(sys.executable))
        for patcher in (helper_path, python_path):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.backend = WaylandBackend()
        self.addCleanup(self.backend.close)
        self.stop = threading.Event()
        self.target = CaptureTarget("portal", "Source du bureau")

    def prepare(self, source):
        self.helper.write_text(_HELPER_CODE + textwrap.dedent(source), encoding="utf-8")
        self.backend.prepare(self.target, stop_event=self.stop)

    def test_bgrx_frames_preserve_stride_and_update_geometry(self):
        self.prepare("""
            send(b'R', 1, 2)
            assert peer.recv(1) == b'G'
            send(b'F', 1, 2, 8, bytes([0, 0, 255, 0, 42, 42, 42, 42,
                                       0, 255, 0, 0, 42, 42, 42, 42]))
            assert peer.recv(1) == b'G'
            send(b'I')
            assert peer.recv(1) == b'G'
            send(b'X', 2, 1, 8, bytes([255, 0, 0, 0, 0, 0, 255, 0]))
            peer.recv(1)
        """)
        initial = self.backend.resolve(self.target)
        self.assertEqual(initial, Rect(0, 0, 1, 2))
        frame = self.backend.grab(initial)
        self.assertEqual(frame.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(frame.getpixel((0, 1)), (0, 255, 0))
        self.assertIsNone(self.backend.grab(initial))
        frame = self.backend.grab(initial)
        self.assertEqual(frame.getpixel((1, 0)), (0, 0, 255))
        self.assertEqual(self.backend.resolve(self.target), Rect(0, 0, 2, 1))

    def test_declined_portal_discards_process_and_keeps_reason(self):
        with self.assertRaisesRegex(CaptureCancelled, "annulé"):
            self.prepare("send(b'C', payload='Partage annulé'.encode())")
        self.assertIsNone(self.backend._process)
        self.backend.close()

    def test_stop_interrupts_blocked_consent(self):
        timer = threading.Timer(0.2, self.stop.set)
        timer.start()
        self.addCleanup(timer.join)
        with self.assertRaisesRegex(CaptureCancelled, "arrêté"):
            self.prepare("peer.recv(1)")
        self.assertIsNone(self.backend._process)
        self.assertIsNone(self.backend._socket)

    def test_stream_revocation_is_reported_without_frame_request(self):
        self.prepare("""
            send(b'R', 1, 1)
            send(b'E', payload='Partage fermé par le bureau'.encode())
        """)
        self.backend._process.wait(timeout=3)
        with self.assertRaisesRegex(CaptureEnded, "fermé par le bureau"):
            self.backend.check_live()

    def test_malformed_frame_is_rejected_before_payload_allocation(self):
        self.prepare("""
            send(b'R', 1, 1)
            assert peer.recv(1) == b'G'
            peer.sendall(header.pack(b'F', 1, 1, 0xffffffff, 0xffffffff))
            peer.recv(1)
        """)
        with self.assertRaisesRegex(CaptureEnded, "image invalide"):
            self.backend.grab(self.backend.resolve(self.target))

    def test_truncated_frames_end_recording(self):
        self.prepare("""
            send(b'R', 1, 2)
            assert peer.recv(1) == b'G'
            peer.sendall(header.pack(b'F', 1, 2, 4, 8) + bytes(4))
        """)
        with self.assertRaisesRegex(CaptureEnded, "fermé par le bureau"):
            self.backend.grab(self.backend.resolve(self.target))


class WaylandEnvironmentTests(unittest.TestCase):
    def test_frozen_app_preserves_system_libraries_for_child(self):
        with patch.object(sys, "frozen", True, create=True), patch.dict(os.environ, {
            "LD_LIBRARY_PATH": "/opt/stepwatch/_internal:/custom",
            "LD_LIBRARY_PATH_ORIG": "/custom",
        }, clear=True):
            self.assertEqual(_system_environment(), {"LD_LIBRARY_PATH": "/custom"})
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/opt/stepwatch/_internal:/custom")


@unittest.skipUnless(sys.platform.startswith("linux") and os.environ.get("STEPWATCH_GSTREAMER_TESTS") == "1",
                     "GStreamer système : activer STEPWATCH_GSTREAMER_TESTS=1.")
class WaylandGStreamerTests(unittest.TestCase):
    def test_stream_accepts_yuv_and_skips_invalid_buffers(self):
        # GI is loaded by the same system interpreter as the portal worker.
        # Replace only the PipeWire input to exercise its real conversion and sink.
        helper = Path(__file__).resolve().parents[1] / "stepwatch" / "_wayland_portal.py"
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-c", textwrap.dedent("""
                import runpy
                import sys

                import gi
                gi.require_version("Gst", "1.0")
                gi.require_version("GstVideo", "1.0")
                from gi.repository import Gst, GstVideo

                Gst.init(None)
                stream = runpy.run_path(sys.argv[1])["_Stream"](Gst, GstVideo, -1, 0)
                status, original = stream.pipeline.iterate_sources().next()
                assert status == Gst.IteratorResult.OK
                next_element = original.get_static_pad("src").get_peer().get_parent_element()
                stream.pipeline.remove(original)
                source = Gst.ElementFactory.make("appsrc")
                source.set_property("is-live", True)
                source.set_property(
                    "caps", Gst.Caps.from_string("video/x-raw,format=NV12,width=16,height=16,framerate=30/1"),
                )
                stream.pipeline.add(source)
                assert source.link(next_element), "Le flux NV12 ne se relie pas à la chaîne de capture."

                def push(flags=None):
                    # A neutral 16 × 16 luma plane and its interleaved UV plane.
                    buffer = Gst.Buffer.new_wrapped(bytes([128]) * 384)
                    if flags is not None:
                        buffer.set_flags(flags)
                    assert source.emit("push-buffer", buffer) == Gst.FlowReturn.OK

                try:
                    stream.start()
                    for flags in (Gst.BufferFlags.CORRUPTED, Gst.BufferFlags.GAP):
                        push(flags)
                        assert stream.sample(250 * Gst.MSECOND) is None, "Image invalide transmise."
                    push()
                    sample = stream.sample(2 * Gst.SECOND)
                    assert sample is not None, "Aucune image convertie."
                    _, width, height, _, stride, size = stream.dimensions(sample)
                    assert (width, height, stride, size) == (16, 16, 64, 1024)
                finally:
                    stream.close()
            """), str(helper)],
            capture_output=True, text=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
