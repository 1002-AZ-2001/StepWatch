import sys
import threading
import unittest
from types import SimpleNamespace

from stepwatch.models import CaptureConfig, CaptureTarget, Rect
from stepwatch.platform import (CaptureUnavailable, WindowsBackend, double_click_settings,
                                enable_dpi_awareness)


class FakeMSS:
    def __init__(self):
        self.monitors = [dict(left=-100, top=0, width=300, height=100),
                         dict(left=-100, top=0, width=100, height=100),
                         SimpleNamespace(left=0, top=0, width=200, height=100)]
        self.closed = False
        self.requests = []

    def grab(self, rectangle):
        self.requests.append(rectangle)
        # Real MSS screenshots expose native BGRA pixels (red, then green).
        return SimpleNamespace(size=(2, 1), bgra=b"\x00\x00\xff\xff\x00\xff\x00\xff")

    def close(self):
        self.closed = True


class FakeWindows:
    exists = True
    minimized = False
    visible = True
    rectangle = Rect(-200, 10, 100, 90)
    hit = 123

    def enumerate_windows(self):
        return [(123, "Terminal"), (456, "AnyDesk")]

    def is_window(self, hwnd): return self.exists
    def is_minimized(self, hwnd): return self.minimized
    def is_visible(self, hwnd): return self.visible
    def window_rect(self, hwnd): return self.rectangle
    def window_at(self, x, y): return self.hit
    def owner(self, hwnd): return getattr(self, "owners", {}).get(hwnd, 0)


class GeometryTests(unittest.TestCase):
    def test_mss_dictionaries_and_objects_both_supported(self):
        data = dict(left=-1920, top=-80, width=1920, height=1080)
        self.assertEqual(Rect.from_any(data), Rect.from_any(SimpleNamespace(**data)))
        self.assertTrue(Rect.from_any(data).contains(-1910, 0))
        self.assertFalse(Rect.from_any(data).contains(0, 0))

    def test_bad_geometry_is_rejected(self):
        for data in ({"left": 0}, {"left": 0, "top": 0, "width": 0, "height": 100},
                     {"left": 0.5, "top": 0, "width": 100, "height": 100}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Rect.from_any(data)

    def test_configuration_validation(self):
        self.assertEqual(CaptureConfig().mode, "hybrid")
        for kwargs in ({"interval": 0}, {"click_delay": -1}, {"sensitivity": float("nan")},
                       {"mode": "keyboard"}, {"masks": ["invalid"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CaptureConfig(**kwargs)


    def test_masks_from_any_iterable_are_kept_and_hwnd_must_be_a_handle(self):
        mask = Rect(0, 0, 10, 10)
        self.assertEqual(CaptureConfig(masks=(mask for _ in range(2))).masks, (mask, mask))
        self.assertEqual(CaptureConfig(masks=[mask]).masks, (mask,))
        for masks in (iter([mask, "x"]), 5):
            with self.subTest(masks=masks), self.assertRaises(ValueError):
                CaptureConfig(masks=masks)
        for hwnd in (True, 0, -5, "123", None):
            with self.subTest(hwnd=hwnd), self.assertRaises(ValueError):
                CaptureTarget("window", "Fenêtre", hwnd=hwnd)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWindows()
        self.screen = FakeMSS()
        self.backend = WindowsBackend(win32=self.native, mss_factory=lambda: self.screen)
        self.target = CaptureTarget("window", "Terminal", hwnd=123)

    def tearDown(self):
        self.backend.close()

    def test_monitor_enumeration_skips_virtual_desktop_and_supports_formats(self):
        monitors = self.backend.list_monitors()
        self.assertEqual(len(monitors), 2)
        self.assertEqual(monitors[0].rect, Rect(-100, 0, 100, 100))
        self.assertEqual(monitors[1].rect, Rect(0, 0, 200, 100))

    def test_window_list_does_not_initialize_monitor_capture(self):
        titles = [target.label for target in self.backend.list_windows()]
        self.assertEqual(titles, ["AnyDesk", "Terminal"])
        self.assertIsNone(self.backend._capture)

    def test_window_rectangle_follows_move_and_resize(self):
        self.assertEqual(self.backend.resolve(self.target), self.native.rectangle)
        self.native.rectangle = Rect(200, -100, 500, 300)
        self.assertEqual(self.backend.resolve(self.target), self.native.rectangle)
        self.assertIsNone(self.backend._capture)

    def test_closed_minimized_hidden_windows_fail_explicitly(self):
        for attribute, value, message in (("exists", False, "fermée"),
                                          ("minimized", True, "réduite"),
                                          ("visible", False, "visible")):
            native = FakeWindows()
            setattr(native, attribute, value)
            backend = WindowsBackend(win32=native)
            with self.subTest(attribute=attribute), self.assertRaisesRegex(CaptureUnavailable, message):
                backend.resolve(self.target)

    def test_clicks_on_occluding_window_and_outside_target_are_ignored(self):
        self.assertTrue(self.backend.belongs_to_target(self.target, -190, 20))
        self.native.hit = 456
        self.assertFalse(self.backend.belongs_to_target(self.target, -190, 20))
        self.assertFalse(self.backend.belongs_to_target(self.target, 0, 0))

    def test_locate_click_is_target_relative_and_never_opens_mss(self):
        self.assertEqual(self.backend.locate_click(self.target, -190, 20), (10, 10))
        self.native.hit = 456
        self.assertIsNone(self.backend.locate_click(self.target, -190, 20))
        self.assertIsNone(self.backend.locate_click(self.target, 0, 0))
        region = CaptureTarget("region", "Zone", rect=Rect(-50, -50, 10, 10))
        self.assertEqual(self.backend.locate_click(region, -45, -41), (5, 9))
        self.assertIsNone(self.backend._capture)

    def test_clicks_on_dialogs_owned_by_the_target_are_kept(self):
        self.native.owners = {789: 123, 790: 789}  # Dialog, then its message box.
        for hwnd in (789, 790):
            self.native.hit = hwnd
            with self.subTest(hwnd=hwnd):
                self.assertEqual(self.backend.locate_click(self.target, -190, 20), (10, 10))
        self.native.hit = 456  # Unrelated window covering the target.
        self.assertIsNone(self.backend.locate_click(self.target, -190, 20))
        self.native.owners = {1: 2, 2: 1}  # A corrupt owner cycle terminates.
        self.native.hit = 1
        self.assertIsNone(self.backend.locate_click(self.target, -190, 20))

    def test_button_probe_for_hook_watchdog_never_raises(self):
        self.assertFalse(self.backend.mouse_button_down())  # Adapter without probe.
        self.native.mouse_button_down = lambda: True
        self.assertTrue(self.backend.mouse_button_down())

        def broken():
            raise OSError("user32 indisponible")
        self.native.mouse_button_down = broken
        self.assertFalse(self.backend.mouse_button_down())

    def test_rgb_capture_and_cleanup(self):
        image = self.backend.grab(Rect(0, 0, 2, 1))
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(image.getpixel((1, 0)), (0, 255, 0))
        self.assertEqual(self.screen.requests, [dict(left=0, top=0, width=2, height=1)])
        self.backend.close()
        self.backend.close()
        self.assertTrue(self.screen.closed)

    def test_fully_offscreen_target_rejected_before_grab(self):
        # Touching the monitor boundary does not give a visible pixel.
        for rectangle in (Rect(-200, 0, 100, 100), Rect(200, 0, 20, 20),
                          Rect(0, 100, 20, 20)):
            with self.subTest(rectangle=rectangle), self.assertRaisesRegex(
                    CaptureUnavailable, "entièrement hors des écrans"):
                self.backend.grab(rectangle)
        self.assertEqual(self.screen.requests, [])

    def test_virtual_desktop_gap_does_not_count_as_physical_monitor(self):
        self.screen.monitors = [dict(left=-100, top=0, width=300, height=100),
                                dict(left=-100, top=0, width=100, height=100),
                                SimpleNamespace(left=100, top=0, width=100, height=100)]
        with self.assertRaisesRegex(CaptureUnavailable, "entièrement hors des écrans"):
            self.backend.grab(Rect(20, 20, 40, 40))
        self.assertEqual(self.screen.requests, [])

    def test_partial_overlap_keeps_target_coordinates_and_supports_monitor_objects(self):
        for rectangle in (Rect(-101, 0, 2, 1), Rect(199, 99, 2, 2)):
            with self.subTest(rectangle=rectangle):
                self.backend.grab(rectangle)
                self.assertEqual(self.screen.requests[-1], rectangle.as_dict())

    def test_no_physical_monitor_has_explicit_capture_error(self):
        self.screen.monitors = self.screen.monitors[:1]
        with self.assertRaisesRegex(CaptureUnavailable, "entièrement hors des écrans"):
            self.backend.grab(Rect(0, 0, 2, 1))
        self.assertEqual(self.screen.requests, [])

    def test_mss_backend_must_not_cross_threads(self):
        self.backend.list_monitors()
        errors = []

        def other_thread():
            try:
                self.backend.list_monitors()
            except CaptureUnavailable as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
        self.assertEqual(len(errors), 1)

    @unittest.skipIf(sys.platform == "win32", "Non-Windows behavior only")
    def test_double_click_settings_fall_back_to_windows_defaults(self):
        self.assertEqual(double_click_settings(), (0.5, 2, 2))
        self.assertEqual(WindowsBackend().double_click_settings(), (0.5, 2, 2))

    @unittest.skipIf(sys.platform == "win32", "Non-Windows behavior only")
    def test_non_windows_import_has_explicit_window_limit(self):
        self.assertFalse(enable_dpi_awareness())
        with self.assertRaisesRegex(CaptureUnavailable, "Windows"):
            WindowsBackend().list_windows()


if __name__ == "__main__":
    unittest.main()
