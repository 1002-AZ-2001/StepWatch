"""Clics par Raw Input : décodage des boutons et boucle de messages simulée (sans Windows)."""

import ctypes
import queue
import sys
import threading
import unittest
from ctypes import wintypes

from stepwatch import rawinput
from stepwatch.engine import click_annotation


class FakeApi:
    """API Win32 simulée : une file de messages WM_INPUT, puis WM_QUIT."""

    def __init__(self, inputs, cursor=(120, 340), register_ok=True):
        self.inputs = list(inputs)
        self.cursor = cursor
        self.register_ok = register_ok
        self.messages: queue.Queue = queue.Queue()
        for index, _ in enumerate(self.inputs):
            self.messages.put((rawinput.WM_INPUT, index))
        self.registered = []
        self.destroyed = False
        self.dispatched = 0

    def GetCurrentThreadId(self):  # noqa: N802
        return threading.get_ident() & 0xFFFFFFFF

    def CreateWindowExW(self, *args):  # noqa: N802
        self.window_args = args
        return 0x1234

    def DestroyWindow(self, window):  # noqa: N802
        self.destroyed = True
        return True

    def RegisterRawInputDevices(self, device_ref, count, size):  # noqa: N802
        device = device_ref._obj
        self.registered.append((device.usUsagePage, device.usUsage, device.dwFlags))
        return self.register_ok

    def GetMessageW(self, message_ref, *args):  # noqa: N802
        kind, value = self.messages.get(timeout=5)
        if kind == rawinput.WM_QUIT:
            return 0
        message = message_ref._obj
        message.message = kind
        message.lParam = value
        return 1

    def GetRawInputData(self, handle, command, data_ref, size_ref, header_size):  # noqa: N802
        data = data_ref._obj
        kind, flags = self.inputs[handle]
        data.header.dwType = kind
        data.mouse.u.buttons.usButtonFlags = flags
        return ctypes.sizeof(data)

    def GetCursorPos(self, point_ref):  # noqa: N802
        point = point_ref._obj
        point.x, point.y = self.cursor
        return True

    def TranslateMessage(self, message_ref):  # noqa: N802
        return True

    def DispatchMessageW(self, message_ref):  # noqa: N802
        self.dispatched += 1
        return 0

    def post_quit(self, thread_id):
        self.messages.put((rawinput.WM_QUIT, 0))


class RawInputTests(unittest.TestCase):
    def test_button_flags_are_decoded_in_order(self):
        self.assertEqual(rawinput.button_events(0x0001), [("Button.left", True)])
        self.assertEqual(rawinput.button_events(0x0002 | 0x0004), [("Button.left", False), ("Button.right", True)])
        self.assertEqual(rawinput.button_events(0x0040 | 0x0200), [("Button.x1", True), ("Button.x2", False)])
        self.assertEqual(rawinput.button_events(0), [])  # Déplacement seul.
        self.assertEqual(click_annotation("Button.left"), "Clic gauche local.")
        self.assertEqual(click_annotation("Button.middle", 2), "Double-clic molette local.")

    @unittest.skipUnless(sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8,
                         "Types Win32 (DWORD 32 bits) sous Windows 64 bits.")
    def test_structures_match_the_windows_x64_layout(self):
        self.assertEqual(ctypes.sizeof(rawinput.RAWINPUTHEADER), 24)
        self.assertEqual(ctypes.sizeof(rawinput.RAWMOUSE), 24)
        self.assertEqual(rawinput.RAWMOUSE.u.offset, 4)
        self.assertEqual(ctypes.sizeof(rawinput.RAWINPUT), 48)
        self.assertEqual(ctypes.sizeof(rawinput.RAWINPUTDEVICE), 16)

    def run_listener(self, api, events):
        listener = rawinput.RawMouseListener(lambda *event: events.append(event), api_factory=lambda: api,
                                             post_quit=api.post_quit)
        listener.start()
        self.assertTrue(listener.wait(5))
        return listener

    def test_clicks_are_reported_with_the_cursor_position_and_moves_are_ignored(self):
        api = FakeApi([(rawinput.RIM_TYPEMOUSE, 0x0001), (rawinput.RIM_TYPEMOUSE, 0),
                       (1, 0x0001),  # Clavier : ignoré.
                       (rawinput.RIM_TYPEMOUSE, 0x0002)])
        events = []
        listener = self.run_listener(api, events)
        deadline = threading.Event()
        for _ in range(200):
            if len(events) >= 2:
                break
            deadline.wait(.01)
        listener.stop()
        listener.join(5)
        self.assertFalse(listener.is_alive())
        self.assertEqual(events, [(120, 340, "Button.left", True), (120, 340, "Button.left", False)])
        # Souris uniquement (page 0x01, usage 0x02), en arrière-plan, puis désinscription.
        self.assertEqual(api.registered[0], (0x01, 0x02, rawinput.RIDEV_INPUTSINK))
        self.assertEqual(api.registered[-1][2], rawinput.RIDEV_REMOVE)
        self.assertTrue(api.destroyed)
        self.assertEqual(api.dispatched, 4)  # DefWindowProc libère chaque WM_INPUT.
        self.assertEqual(api.window_args[1], "STATIC")
        self.assertEqual(api.window_args[8].value, wintypes.HWND(-3).value)  # HWND_MESSAGE

    def test_callback_errors_do_not_stop_the_listener(self):
        api = FakeApi([(rawinput.RIM_TYPEMOUSE, 0x0001), (rawinput.RIM_TYPEMOUSE, 0x0004)])
        seen = []

        def callback(x, y, button, pressed):
            seen.append(button)
            if button == "Button.left":
                raise RuntimeError("clic en erreur")

        listener = rawinput.RawMouseListener(callback, api_factory=lambda: api, post_quit=api.post_quit)
        with self.assertLogs("stepwatch.rawinput", level="ERROR"):
            listener.start()
            for _ in range(200):
                if len(seen) >= 2:
                    break
                threading.Event().wait(.01)
        listener.stop()
        listener.join(5)
        self.assertEqual(seen, ["Button.left", "Button.right"])

    def test_registration_failure_is_reported_and_the_thread_ends(self):
        api = FakeApi([], register_ok=False)
        listener = rawinput.RawMouseListener(lambda *event: None, api_factory=lambda: api, post_quit=api.post_quit)
        with self.assertLogs("stepwatch.rawinput", level="ERROR"):
            listener.start()
            self.assertTrue(listener.wait(5))
            listener.join(5)
        self.assertFalse(listener.is_alive())
        self.assertIn("RegisterRawInputDevices", listener.error)

    def test_stop_before_start_is_harmless(self):
        listener = rawinput.RawMouseListener(lambda *event: None, api_factory=FakeApi, post_quit=lambda _: None)
        listener.stop()
        self.assertFalse(listener.is_alive())


if __name__ == "__main__":
    unittest.main()
