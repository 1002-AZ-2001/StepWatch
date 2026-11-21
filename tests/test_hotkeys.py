"""System-wide recording shortcuts, with a fake Win32 API."""

import queue
import time
import unittest

from stepwatch.hotkeys import LABELS, SHORTCUTS, GlobalHotkeys


class FakeApi:
    def __init__(self, taken=()):
        self.taken = set(taken)
        self.registered = {}
        self.unregistered = []
        self.messages = queue.Queue()

    def thread_id(self):
        return 42

    def register(self, identifier, modifiers, key):
        if key in self.taken:
            return False
        self.registered[identifier] = (modifiers, key)
        return True

    def unregister(self, identifier):
        self.unregistered.append(identifier)

    def next_hotkey(self):
        return self.messages.get(timeout=5)

    def post_quit(self, thread_id):
        assert thread_id == 42
        self.messages.put(None)


class GlobalHotkeyTests(unittest.TestCase):
    def test_only_windows_supports_global_hotkeys(self):
        self.assertTrue(GlobalHotkeys.supported("win32"))
        self.assertFalse(GlobalHotkeys.supported("linux"))

    def test_hotkeys_become_actions_and_are_released_on_stop(self):
        api = FakeApi()
        hotkeys = GlobalHotkeys(api_factory=lambda: api)
        self.assertTrue(hotkeys.start())
        self.assertEqual(sorted(api.registered), [1, 2, 3])
        for identifier in (1, 2, 3, 99):  # 99: not ours, ignored.
            api.messages.put(identifier)
        deadline = time.monotonic() + 5
        actions = []
        while len(actions) < 3 and time.monotonic() < deadline:
            try:
                actions.append(hotkeys.events.get(timeout=.1))
            except queue.Empty:
                pass
        self.assertEqual(actions, ["pause", "note", "stop"])
        hotkeys.stop()
        self.assertEqual(sorted(api.unregistered), [1, 2, 3])

    def test_combinations_used_elsewhere_are_reported(self):
        api = FakeApi(taken={SHORTCUTS[1][2]})
        hotkeys = GlobalHotkeys(api_factory=lambda: api)
        hotkeys.start()
        self.assertEqual(hotkeys.failed, [LABELS["note"]])
        hotkeys.stop()
        self.assertEqual(sorted(api.unregistered), [1, 3])  # Only what was registered.

    def test_function_keys_avoid_f12_reserved_by_windows(self):
        self.assertNotIn(0x7B, [key for _, _, key, _ in SHORTCUTS])


if __name__ == "__main__":
    unittest.main()
