"""Recorder integration tests with real PNG/JSON/HTML storage and fake OS hooks."""

import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from stepwatch.engine import Recorder
from stepwatch.models import CaptureConfig, CaptureTarget, Rect
from stepwatch.platform import CaptureUnavailable
from stepwatch.storage import SessionStore, StorageError


class FakeBackend:
    """Deterministic screen adapter, including a controllable in-flight grab."""

    def __init__(self):
        self.rectangle = Rect(-20, 10, 80, 60)
        self.image = Image.new("RGB", (80, 60), "black")
        self.error = None
        self.closed = False
        self.owner = None
        self.closed_on = None
        self.grab_times = []
        self.block_next = False
        self.grab_entered = threading.Event()
        self.release_grab = threading.Event()

    def resolve(self, target):
        self.owner = threading.get_ident()
        if self.error:
            raise CaptureUnavailable(self.error)
        return self.rectangle

    def belongs_to_target(self, target, x, y):
        return self.resolve(target).contains(x, y)

    def grab(self, rectangle):
        self.grab_times.append(time.monotonic())
        if self.block_next:
            self.block_next = False
            self.grab_entered.set()
            if not self.release_grab.wait(3):
                raise RuntimeError("Test backend was not released")
        return self.image.copy()

    def close(self):
        self.closed = True
        self.closed_on = threading.get_ident()


class FakeListener:
    def __init__(self, callback):
        self.callback = callback
        self.started = threading.Event()
        self.alive = False
        self.stops = 0
        self.joins = 0

    def start(self):
        self.alive = True
        self.started.set()

    def click(self, x=-10, y=20, pressed=True):
        self.callback(x, y, "Button.left", pressed)

    def stop(self):
        self.stops += 1
        self.alive = False

    def join(self, timeout=None):
        self.joins += 1

    def is_alive(self):
        return self.alive


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base_dir = Path(self.temporary.name)
        self.backend = FakeBackend()
        self.listeners = []
        self.observed_events = []

    def make_recorder(self, *, mode="hybrid", interval=.1, click_delay=.1,
                      masks=(), backend_factory=None, listener_factory=None):
        self.store = SessionStore(self.base_dir, "Intervention de test")

        def create_listener(callback):
            listener = FakeListener(callback)
            self.listeners.append(listener)
            return listener

        self.recorder = Recorder(
            CaptureTarget("window", "Client distant", hwnd=123),
            CaptureConfig(mode=mode, interval=interval, click_delay=click_delay,
                          sensitivity=2, masks=masks),
            self.store,
            backend_factory=backend_factory or (lambda: self.backend),
            listener_factory=listener_factory or create_listener,
        )
        self.addCleanup(self.shutdown)
        return self.recorder

    def shutdown(self):
        self.backend.release_grab.set()
        self.recorder.stop(timeout=3)

    def wait_for(self, predicate, message="Condition did not become true", timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain_events()
            if predicate():
                return
            time.sleep(.005)
        self.drain_events()
        self.fail(message + f"; state={self.recorder.state}, events={self.observed_events}")

    def drain_events(self):
        if not hasattr(self, "recorder"):
            return
        while True:
            try:
                event = self.recorder.events.get_nowait()
                # Avoid large image representations in assertion diagnostics.
                event.pop("image", None)
                self.observed_events.append(event)
            except queue.Empty:
                return

    def start(self):
        self.recorder.start()
        if self.recorder.config.mode != "visual":
            self.wait_for(lambda: bool(self.listeners) and self.listeners[0].started.is_set())
        return self.listeners[0] if self.listeners else None

    def saved_events(self):
        self.drain_events()
        return [event for event in self.observed_events if event["type"] == "saved"]

    def test_clicks_mode_delays_and_debounces_press_release(self):
        self.make_recorder(mode="clicks", click_delay=.15)
        listener = self.start()
        clicked_at = time.monotonic()
        listener.click()
        listener.click()  # Duplicate hook notification of the same press.
        listener.click(pressed=False)
        time.sleep(.04)
        self.assertEqual(self.store.steps, [])
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertGreaterEqual(self.backend.grab_times[0] - clicked_at, .12)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(len(self.store.steps), 1)
        self.assertEqual(self.store.steps[0]["trigger"], "local_click")
        self.assertEqual(self.store.steps[0]["click"], [10, 10])
        self.assertEqual(listener.stops, 1)
        self.assertEqual(listener.joins, 1)
        self.assertTrue(self.backend.closed)
        self.assertEqual(self.backend.owner, self.backend.closed_on)

    def test_real_separate_clicks_remain_separate_steps(self):
        self.make_recorder(mode="clicks", click_delay=.04)
        listener = self.start()
        listener.click()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        listener.click()
        self.wait_for(lambda: len(self.saved_events()) == 2)
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["trigger"] for step in self.store.steps],
                         ["local_click", "local_click"])

    def test_visual_mode_records_changes_without_creating_mouse_hook(self):
        self.make_recorder(mode="visual")
        self.start()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertEqual(self.listeners, [])
        self.backend.image = Image.new("RGB", (80, 60), "white")
        self.wait_for(lambda: len(self.saved_events()) == 2)
        self.recorder._on_click(-10, 20, "Button.left", True)
        self.wait_for(lambda: any(event["type"] == "ignored" for event in self.observed_events))
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["trigger"] for step in self.store.steps],
                         ["initial", "visual_change"])
        self.assertIn("origine non déterminée", self.store.steps[1]["annotation"])

    def test_hybrid_defers_visual_sample_while_click_is_pending(self):
        self.make_recorder(mode="hybrid", interval=.1, click_delay=.15)
        listener = self.start()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.backend.image = Image.new("RGB", (80, 60), "white")
        listener.click()
        self.wait_for(lambda: len(self.saved_events()) == 2)
        self.wait_for(lambda: any(event["type"] == "ignored" for event in self.observed_events))
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["trigger"] for step in self.store.steps],
                         ["initial", "local_click"])

    def test_click_arriving_during_visual_grab_does_not_create_duplicate_visual_step(self):
        self.make_recorder(mode="hybrid", interval=.1, click_delay=.05)
        listener = self.start()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.backend.image = Image.new("RGB", (80, 60), "white")
        self.backend.block_next = True
        self.assertTrue(self.backend.grab_entered.wait(2))
        listener.click()
        self.backend.release_grab.set()
        self.wait_for(lambda: any(step["trigger"] == "local_click" for step in self.store.steps))
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["trigger"] for step in self.store.steps],
                         ["initial", "local_click"])

    def test_pause_cancels_pending_and_during_pause_clicks(self):
        self.make_recorder(mode="clicks", click_delay=.2)
        listener = self.start()
        listener.click()
        self.recorder.pause()
        listener.click(x=0)
        self.recorder.resume()
        # Different coordinates avoid the hook duplicate threshold.
        listener.click(x=1)
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(len(self.store.steps), 1)
        self.assertEqual(self.store.steps[0]["click"], [21, 10])

    def test_pause_during_grab_discards_image_and_resumes_cleanly(self):
        self.make_recorder(mode="visual")
        self.backend.block_next = True
        self.start()
        self.assertTrue(self.backend.grab_entered.wait(2))
        self.recorder.pause()
        self.backend.release_grab.set()
        self.wait_for(lambda: any("Capture annulée" in event["message"]
                                  for event in self.observed_events))
        self.assertEqual(self.store.steps, [])
        self.recorder.resume()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.store.steps[0]["trigger"], "initial")

    def test_window_temporarily_unavailable_recovers_with_explicit_error(self):
        self.make_recorder(mode="visual")
        self.start()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.backend.error = "La fenêtre est réduite."
        self.wait_for(lambda: any(event["type"] == "error" for event in self.observed_events))
        self.backend.error = None
        self.backend.image = Image.new("RGB", (80, 60), "white")
        self.wait_for(lambda: len(self.saved_events()) == 2)
        self.assertTrue(self.recorder.stop())
        self.assertTrue(any("de nouveau disponible" in event["message"]
                            for event in self.observed_events))

    def test_initial_window_failure_closes_backend_and_reports(self):
        self.make_recorder(mode="visual")
        self.backend.error = "La fenêtre sélectionnée a été fermée."
        self.recorder.start()
        self.wait_for(lambda: self.recorder.state == "error")
        self.assertTrue(self.recorder.stop())
        self.assertTrue(self.backend.closed)
        self.assertEqual(self.listeners, [])
        self.assertEqual(self.store.steps, [])
        self.assertTrue((self.store.directory / "rapport.html").is_file())

    def test_mouse_hook_startup_failure_closes_all_created_resources(self):
        class FailedListener(FakeListener):
            def start(inner):
                raise RuntimeError("Hook indisponible")

        listener = FailedListener(None)
        self.make_recorder(mode="clicks", listener_factory=lambda callback: listener)
        self.recorder.start()
        self.wait_for(lambda: self.recorder.state == "error")
        self.assertTrue(self.recorder.stop())
        self.assertEqual(listener.stops, 1)
        self.assertTrue(self.backend.closed)
        self.assertTrue(any("Hook indisponible" in event["message"] for event in self.observed_events))

    def test_hook_dying_during_recording_is_reported(self):
        self.make_recorder(mode="clicks")
        listener = self.start()
        listener.alive = False
        self.wait_for(lambda: self.recorder.state == "error", "Dead mouse hook was not reported")
        self.assertTrue(self.recorder.stop())
        self.assertTrue(self.backend.closed)

    def test_storage_failure_stops_recording_without_silent_loss(self):
        self.make_recorder(mode="visual")
        with patch.object(self.store, "save", side_effect=StorageError("Disque plein")):
            self.recorder.start()
            self.wait_for(lambda: self.recorder.state == "error")
            self.assertTrue(self.recorder.stop())
        self.assertTrue(self.backend.closed)
        self.assertEqual(self.saved_events(), [])
        self.assertTrue(any("Disque plein" in event["message"] for event in self.observed_events))

    def test_report_write_failure_at_stop_is_explicit(self):
        self.make_recorder(mode="visual")
        self.start()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        with patch.object(self.store, "close", side_effect=StorageError("Rapport non inscriptible")):
            self.assertTrue(self.recorder.stop())
        self.drain_events()
        self.assertEqual(self.recorder.state, "error")
        self.assertTrue(self.backend.closed)
        self.assertTrue(any("Rapport non inscriptible" in event["message"]
                            for event in self.observed_events))
        # Already committed captures stay readable despite final report failure.
        self.assertTrue((self.store.directory / self.store.steps[0]["image"]).is_file())

    def test_thread_start_failure_allows_safe_stop(self):
        self.make_recorder(mode="visual")
        with patch("stepwatch.engine.threading.Thread.start", side_effect=RuntimeError("Thread indisponible")):
            with self.assertRaisesRegex(RuntimeError, "Thread indisponible"):
                self.recorder.start()
        self.assertEqual(self.recorder.state, "error")
        self.assertTrue(self.recorder.stop())
        self.assertFalse(self.backend.closed)  # Backend was never instantiated.
        self.assertTrue((self.store.directory / "rapport.html").is_file())

    def test_stop_waits_for_inflight_grab_and_does_not_save_after_stop(self):
        self.make_recorder(mode="visual")
        self.backend.block_next = True
        self.start()
        self.assertTrue(self.backend.grab_entered.wait(2))
        self.assertFalse(self.recorder.stop(timeout=.01))
        self.assertEqual(self.recorder.state, "stopping")
        self.backend.release_grab.set()
        self.assertTrue(self.recorder.stop(timeout=3))
        self.assertEqual(self.store.steps, [])
        self.assertTrue(self.backend.closed)
        self.assertEqual(self.recorder.state, "stopped")

    def test_stop_without_start_writes_valid_empty_report(self):
        self.make_recorder()
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.recorder.state, "stopped")
        metadata = json.loads((self.store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["steps"], [])
        self.assertEqual(self.listeners, [])
        self.assertFalse(self.backend.closed)

    def test_redaction_applies_to_saved_image_and_click_marker(self):
        self.make_recorder(mode="clicks", click_delay=0,
                           masks=(Rect(0, 0, 30, 30),))
        self.backend.image = Image.new("RGB", (80, 60), "white")
        listener = self.start()
        listener.click()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertTrue(self.recorder.stop())
        with Image.open(self.store.directory / self.store.steps[0]["image"]) as saved:
            self.assertEqual(saved.getpixel((10, 10)), (0, 0, 0))
            self.assertEqual(saved.getpixel((40, 40)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
