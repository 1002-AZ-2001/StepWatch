"""X11 geometry and capture checks, including optional real-server tests."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from stepwatch.engine import Recorder
from stepwatch.models import CaptureConfig, CaptureTarget, Rect
from stepwatch.platform import CaptureUnavailable
from stepwatch.storage import SessionStore
from stepwatch.x11 import X11Backend, X11ClickListener


class X11GeometryTests(unittest.TestCase):
    def test_partial_capture_keeps_mask_and_click_coordinates(self):
        backend = X11Backend()
        desktop = Mock()
        desktop.desktop_rect.return_value = Rect(0, 0, 10, 8)
        screen = Mock()
        screen.grab.return_value = SimpleNamespace(size=(2, 1), raw=bytearray.fromhex("0000ff0000ff0000"))
        rectangle = Rect(-2, 7, 4, 2)
        with patch.object(backend, "_x11", return_value=desktop), patch.object(backend, "_screen", return_value=screen):
            image = backend.grab(rectangle)
            with self.assertRaisesRegex(CaptureUnavailable, "hors du bureau X11"):
                backend.grab(Rect(10, 0, 1, 1))
            with self.assertRaisesRegex(CaptureUnavailable, "trop grande"):
                backend.grab(Rect(0, 0, 100_000, 100_000))
        screen.grab.assert_called_once_with(Rect(0, 7, 2, 1).as_dict())
        self.assertEqual(image.size, (4, 2))
        self.assertEqual(image.getpixel((1, 0)), (0, 0, 0))
        self.assertEqual(image.getpixel((2, 0)), (255, 0, 0))
        self.assertEqual(image.getpixel((3, 0)), (0, 255, 0))
        self.assertEqual(image.getpixel((2, 1)), (0, 0, 0))

    def test_region_click_does_not_open_the_capture_or_window_connection(self):
        backend = X11Backend()
        target = CaptureTarget("region", "Zone", rect=Rect(-5, 0, 10, 10))
        self.assertEqual(backend.locate_click(target, 2, 8), (7, 8))
        self.assertIsNone(backend.locate_click(target, 5, 10))
        self.assertIsNone(backend._desktop)
        self.assertIsNone(backend._capture)


@unittest.skipUnless(sys.platform.startswith("linux") and os.environ.get("STEPWATCH_X11_TESTS") == "1",
                     "X11 réel : activer STEPWATCH_X11_TESTS=1 sur un serveur de test isolé.")
class X11ServerTests(unittest.TestCase):
    def setUp(self):
        from Xlib import X, display

        self.X = X
        self.client = display.Display()
        self.addCleanup(self.client.close)
        self.root = self.client.screen().root
        self.backend = X11Backend()
        self.addCleanup(self.backend.close)

    def _create_window(self, title, x, y, color):
        window = self.root.create_window(x, y, 60, 50, 0, self.client.screen().root_depth,
                                         background_pixel=color)
        self.addCleanup(window.destroy)
        utf8_atom = self.client.intern_atom("UTF8_STRING")
        title_atom = self.client.intern_atom("_NET_WM_NAME")
        window.change_property(title_atom, utf8_atom, 8, title.encode("utf-8"))
        window.map()
        self.client.sync()
        return window

    def test_window_pixels_geometry_and_clicks_on_an_overlapping_window(self):
        title = "Étapes · X11"
        window = self._create_window(title, 40, 60, 0xEF4422)
        target = next(target for target in self.backend.list_windows() if target.hwnd == window.id)
        self.assertEqual(target.label, title)
        self.assertEqual(self.backend.resolve(target), Rect(40, 60, 60, 50))
        self.assertTrue(any(monitor.rect.contains(51, 70) for monitor in self.backend.list_monitors()))
        frame = self.backend.grab(self.backend.resolve(target))
        self.assertEqual(frame.mode, "RGB")
        self.assertEqual(frame.getpixel((15, 10)), (239, 68, 34))
        self.assertEqual(self.backend.locate_click(target, 51, 70), (11, 10))

        overlap = self._create_window("Superposition", 50, 55, 0x1133EE)
        self.assertIsNone(self.backend.locate_click(target, 51, 70))
        overlap.set_wm_transient_for(window)
        self.client.sync()
        self.assertEqual(self.backend.locate_click(target, 51, 70), (11, 10))
        overlap.unmap()
        window.configure(x=70, y=80, width=65)
        self.client.sync()
        self.assertEqual(self.backend.resolve(target), Rect(70, 80, 65, 50))
        window.unmap()
        self.client.sync()
        with self.assertRaisesRegex(CaptureUnavailable, "masquée ou réduite"):
            self.backend.resolve(target)

    def test_listener_can_stop_without_waiting_for_another_click(self):
        listener = X11ClickListener(Mock())
        listener.start()
        time.sleep(.05)
        listener.stop()
        listener.stop()
        listener.join(timeout=1)
        self.assertFalse(listener.is_alive())

    def test_native_recorded_click_preserves_coordinates_and_masks(self):
        from Xlib.ext import xtest

        window = self._create_window("Capture XRecord", 50, 50, 0x123456)
        target = CaptureTarget("window", "Capture XRecord", hwnd=window.id)
        with TemporaryDirectory() as temporary:
            store = SessionStore(Path(temporary), "Clic X11 réel")
            config = CaptureConfig(mode="clicks", click_delay=.03, masks=(Rect(3, 3, 8, 8),))
            recorder = Recorder(target, config, store, backend_factory=X11Backend)
            recorder.start()
            try:
                # The first injected event can arrive before XRecord is ready.
                for _ in range(12):
                    if store.steps or recorder.state == "error":
                        break
                    xtest.fake_input(self.client, self.X.MotionNotify, root=self.root, x=76, y=74)
                    xtest.fake_input(self.client, self.X.ButtonPress, detail=1)
                    xtest.fake_input(self.client, self.X.ButtonRelease, detail=1)
                    self.client.sync()
                    time.sleep(.24)
            finally:
                self.assertTrue(recorder.stop())
            self.assertEqual(recorder.state, "stopped", list(recorder.events.queue))
            metadata = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(metadata["steps"]), 1)
            step = metadata["steps"][0]
            self.assertEqual(step["trigger"], "local_click")
            self.assertEqual(step["click"], [26, 24])
            with Image.open(store.directory / step["image"]) as image:
                self.assertEqual(image.getpixel((5, 5)), (0, 0, 0))
                self.assertEqual(image.getpixel((56, 45)), (18, 52, 86))
            self.assertTrue((store.directory / "rapport.html").is_file())


if __name__ == "__main__":
    unittest.main()
