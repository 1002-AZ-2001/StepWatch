"""Recorder integration tests with real PNG/JSON/HTML storage and fake OS hooks."""

import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from stepwatch.engine import Recorder, click_annotation
from stepwatch.models import CaptureConfig, CaptureTarget, Rect
from stepwatch.platform import CaptureCancelled, CaptureEnded, CaptureUnavailable
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
        self.hit = True  # False: another window covers the click point.
        self.locate_threads = []

    def resolve(self, target):
        self.owner = threading.get_ident()
        if self.error:
            raise CaptureUnavailable(self.error)
        return self.rectangle

    def belongs_to_target(self, target, x, y):
        return self.hit and self.resolve(target).contains(x, y)

    def locate_click(self, target, x, y):
        # Runs on the hook thread: must not change the capture owner.
        self.locate_threads.append(threading.get_ident())
        if self.error:
            raise CaptureUnavailable(self.error)
        if not (self.hit and self.rectangle.contains(x, y)):
            return None
        return x - self.rectangle.left, y - self.rectangle.top

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


class LegacyBackend(FakeBackend):
    """A backend without hook-time hit-testing: the worker checks clicks."""

    locate_click = None


class FakePortalBackend(FakeBackend):
    uses_portal = True

    def __init__(self):
        super().__init__()
        self.awaiting_selection = threading.Event()
        self.selection_done = threading.Event()
        self.selection_error = None
        self.stream_ended = False
        self.grab_error = None

    def prepare(self, target, *, stop_event):
        self.awaiting_selection.set()
        deadline = time.monotonic() + 2
        while not self.selection_done.wait(.01):
            if stop_event.is_set():
                raise CaptureCancelled("Arrêt demandé par StepWatch")
            if time.monotonic() >= deadline:
                raise RuntimeError("Le test n’a pas libéré la sélection")
        if self.selection_error is not None:
            raise self.selection_error

    def check_live(self):
        if self.stream_ended:
            raise CaptureEnded("Le partage de la source a été arrêté.")

    def grab(self, rectangle):
        if self.grab_error is not None:
            raise self.grab_error
        frame = super().grab(rectangle)
        return frame if len(self.grab_times) == 1 else None


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

    def make_portal_recorder(self, mode="visual"):
        self.backend = FakePortalBackend()
        factory = Mock(return_value=self.backend, uses_portal=True)
        self.make_recorder(mode=mode, backend_factory=factory)
        return self.recorder

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

    def test_click_captures_do_not_run_the_visual_comparison(self):
        self.make_recorder(mode="clicks", click_delay=0)
        with patch("stepwatch.engine.VisualComparator",
                   side_effect=AssertionError("comparaison inutile")) as compare:
            listener = self.start()
            listener.click()
            self.wait_for(lambda: len(self.saved_events()) == 1)
            self.assertTrue(self.recorder.stop())
        compare.assert_not_called()
        self.assertEqual(self.recorder.state, "stopped")

    def test_click_is_located_when_it_happens_not_when_worker_is_free(self):
        # The worker is busy (grab, comparison, PNG) while the application
        # reacts: a context menu now covers the point and the window moved.
        self.make_recorder(mode="hybrid", interval=.1, click_delay=0)
        listener = self.start()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.backend.block_next = True
        self.assertTrue(self.backend.grab_entered.wait(2))
        listener.click()
        self.backend.hit = False
        self.backend.rectangle = Rect(0, 10, 80, 60)
        self.backend.release_grab.set()
        self.wait_for(lambda: any(step["trigger"] == "local_click" for step in self.store.steps))
        self.assertTrue(self.recorder.stop())
        step = next(step for step in self.store.steps if step["trigger"] == "local_click")
        self.assertEqual(step["click"], [10, 10])
        self.assertEqual(self.backend.locate_threads, [threading.get_ident()])

    def test_clicks_outside_target_are_dropped_at_click_time(self):
        self.make_recorder(mode="clicks", click_delay=0)
        listener = self.start()
        self.backend.hit = False
        listener.click()
        time.sleep(.15)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.store.steps, [])
        self.assertTrue(self.recorder._clicks.empty())

    def test_click_overflow_does_not_block_hook_and_is_reported_on_stop(self):
        with patch("stepwatch.engine.MAX_PENDING_CLICKS", 3):
            self.make_recorder(mode="clicks", click_delay=0)
            self.backend.block_next = True
            listener = self.start()
            listener.click()
            self.assertTrue(self.backend.grab_entered.wait(1))
            for index in range(30):
                listener.click(x=0 if index % 2 == 0 else -10)
            self.assertEqual(self.recorder._clicks.qsize(), 3)
            self.assertFalse(self.recorder.stop(timeout=0))
            self.backend.release_grab.set()
            self.assertTrue(self.recorder.stop())
        self.drain_events()
        reports = [event for event in self.observed_events if "saturation" in event["message"]]
        self.assertEqual(len(reports), 1)
        self.assertIn("(27)", reports[0]["message"])
        self.assertTrue(self.recorder._clicks.empty())

    def test_delayed_clicks_share_capacity_and_pause_releases_their_slots(self):
        with patch("stepwatch.engine.MAX_PENDING_CLICKS", 3):
            self.make_recorder(mode="clicks", click_delay=20)
            listener = self.start()
            listener.click(x=-10)
            listener.click(x=0)
            listener.click(x=10)
            self.wait_for(lambda: self.recorder._clicks.empty())
            listener.click(x=20)
            self.wait_for(lambda: any("saturation" in event["message"] for event in self.observed_events))
            self.recorder.pause()
            self.wait_for(lambda: self.observed_events[-1]["ignored"] == 4)
            self.recorder.resume()
            listener.click(x=0)
            self.wait_for(lambda: self.recorder._clicks.empty())
            self.recorder.pause()
            self.wait_for(lambda: self.observed_events[-1]["ignored"] == 5)
            self.assertTrue(self.recorder.stop())
        self.assertEqual(sum("saturation" in event["message"] for event in self.observed_events), 1)
        self.assertEqual(self.saved_events(), [])

    def test_backend_without_locator_or_failing_locator_falls_back_to_worker(self):
        legacy = LegacyBackend()
        self.make_recorder(mode="clicks", click_delay=0, backend_factory=lambda: legacy)
        listener = self.start()
        listener.click()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.store.steps[0]["click"], [10, 10])

        self.observed_events.clear()
        self.listeners.clear()
        self.backend = FakeBackend()
        self.make_recorder(mode="clicks", click_delay=0)
        listener = self.start()
        self.backend.error = "La fenêtre sélectionnée est réduite. Restaurez-la."
        listener.click()  # The hook must not raise into pynput.
        self.wait_for(lambda: any(event["type"] == "error" for event in self.observed_events))
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.store.steps, [])

    def test_double_click_is_one_step_captured_after_the_second_press(self):
        self.make_recorder(mode="clicks", click_delay=.2)
        listener = self.start()
        listener.click()
        time.sleep(.08)
        second_press = time.monotonic()
        listener.click(x=-9)  # A real second press may move by a pixel.
        self.wait_for(lambda: len(self.saved_events()) == 1)
        time.sleep(.3)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(len(self.store.steps), 1)
        step = self.store.steps[0]
        self.assertEqual((step["click_count"], step["click"]), (2, [10, 10]))
        self.assertEqual(step["annotation"], "Double-clic gauche local.")
        self.assertGreaterEqual(self.backend.grab_times[0] - second_press, .17)
        report = (self.store.directory / "rapport.html").read_text(encoding="utf-8")
        self.assertIn("Étape 1 — Double-clic local", report)

    def test_triple_click_is_one_step(self):
        self.make_recorder(mode="clicks", click_delay=.2)
        listener = self.start()
        for x in (-10, -9, -10):
            listener.click(x=x)
            time.sleep(.06)
        self.wait_for(lambda: len(self.saved_events()) == 1)
        time.sleep(.3)
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["click_count"] for step in self.store.steps], [3])
        self.assertEqual(self.store.steps[0]["annotation"], "Triple-clic gauche local.")

    def test_clicks_apart_in_space_time_or_button_stay_separate(self):
        self.backend.double_click_settings = lambda: (.1, 2, 2)
        self.make_recorder(mode="clicks", click_delay=.3)
        listener = self.start()
        listener.click()
        time.sleep(.04)
        listener.click(x=-5)  # Outside the double-click rectangle.
        time.sleep(.04)
        listener.callback(-10, 20, "Button.right", True)  # Another button.
        time.sleep(.15)
        listener.click()  # Same place, but after the double-click time.
        self.wait_for(lambda: len(self.saved_events()) == 4)
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["click_count"] for step in self.store.steps], [1, 1, 1, 1])
        self.assertEqual([step["annotation"] for step in self.store.steps],
                         ["Clic gauche local.", "Clic gauche local.", "Clic droit local.",
                          "Clic gauche local."])

    def test_nonconsecutive_spatial_clicks_do_not_form_a_double_click(self):
        self.make_recorder(mode="clicks", click_delay=.1)
        listener = self.start()
        # Keep the worker from draining until every press is queued. A-B-A
        # must stay chronological; the final A cannot join the first A.
        with self.recorder._lock:
            listener.click(x=-10)
            listener.click(x=0)
            listener.click(x=-10)
        self.wait_for(lambda: len(self.store.steps) == 3)
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["click_count"] for step in self.store.steps], [1, 1, 1])
        self.assertEqual([step["click"] for step in self.store.steps],
                         [[10, 10], [20, 10], [10, 10]])

    def test_intervening_button_press_ends_double_click_sequence(self):
        self.make_recorder(mode="clicks", click_delay=.1)
        listener = self.start()
        with self.recorder._lock:
            listener.click()
            listener.callback(-10, 20, "Button.right", True)
            listener.click()
        self.wait_for(lambda: len(self.store.steps) == 3)
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["click_count"] for step in self.store.steps], [1, 1, 1])
        self.assertEqual([step["annotation"] for step in self.store.steps],
                         ["Clic gauche local.", "Clic droit local.", "Clic gauche local."])

    def test_click_annotations_are_french(self):
        self.assertEqual(click_annotation("Button.right"), "Clic droit local.")
        self.assertEqual(click_annotation("Button.middle", 4), "Clic molette local répété 4 fois.")
        self.assertEqual(click_annotation("Button.unknown", 2), "Double-clic Button.unknown local.")

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

    def test_portal_waits_for_selection_before_recording_and_skips_empty_samples(self):
        recorder = self.make_portal_recorder()
        recorder.start()
        self.assertTrue(self.backend.awaiting_selection.wait(1))
        self.assertEqual(recorder.state, "starting")
        self.assertEqual(self.backend.grab_times, [])
        self.backend.selection_done.set()
        self.wait_for(lambda: len(self.backend.grab_times) >= 3)
        self.assertEqual(recorder.state, "recording")
        self.assertEqual(self.listeners, [])
        self.assertTrue(recorder.stop())
        self.assertEqual(len(self.saved_events()), 1)
        self.assertTrue(all(event["ignored"] == 0 for event in self.observed_events))

    def test_portal_cancel_closes_resources_without_error(self):
        recorder = self.make_portal_recorder()
        self.backend.selection_error = CaptureCancelled("Sélection annulée")
        self.backend.selection_done.set()
        recorder.start()
        self.wait_for(lambda: recorder.state == "stopped")
        self.assertTrue(self.backend.closed)
        self.assertFalse(any(event["type"] == "error" for event in self.observed_events))
        self.assertIn("annulée", self.observed_events[-1]["message"])

    def test_portal_stop_cancels_pending_selection(self):
        recorder = self.make_portal_recorder()
        recorder.start()
        self.assertTrue(self.backend.awaiting_selection.wait(1))
        self.assertTrue(recorder.stop(timeout=1))
        self.assertEqual(recorder.state, "stopped")
        self.assertTrue(self.backend.closed)
        self.drain_events()
        self.assertFalse(any(event["type"] == "error" or "démarré" in event["message"]
                             for event in self.observed_events))

    def test_stopping_during_a_portal_frame_is_not_an_error(self):
        recorder = self.make_portal_recorder()
        reading = threading.Event()

        def grab(_rectangle):
            reading.set()
            if recorder._stop.wait(2):
                raise CaptureCancelled("Partage Wayland arrêté.")
            raise RuntimeError("Le test n'a pas arrêté la lecture.")

        with patch.object(self.backend, "grab", side_effect=grab):
            self.backend.selection_done.set()
            recorder.start()
            self.assertTrue(reading.wait(1))
            self.assertTrue(recorder.stop(timeout=1))
        self.assertEqual(recorder.state, "stopped")
        self.assertTrue(self.backend.closed)
        self.drain_events()
        self.assertFalse(any(event["type"] == "error" for event in self.observed_events))

    def test_portal_rejects_non_visual_mode_before_asking_for_a_source(self):
        recorder = self.make_portal_recorder(mode="hybrid")
        recorder.start()
        self.wait_for(lambda: recorder.state == "error")
        self.assertFalse(self.backend.awaiting_selection.is_set())
        self.assertTrue(self.backend.closed)
        self.assertEqual(self.listeners, [])
        self.assertTrue(any("mode de capture visuel" in event["message"]
                            for event in self.observed_events if event["type"] == "error"))

    def test_portal_end_during_grab_keeps_the_recording_without_retry(self):
        recorder = self.make_portal_recorder()
        self.backend.selection_done.set()
        recorder.start()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.backend.grab_error = CaptureEnded("Le flux est terminé.")
        self.wait_for(lambda: recorder.state == "error")
        self.assertTrue(self.backend.closed)
        errors = [event["message"] for event in self.observed_events if event["type"] == "error"]
        self.assertEqual(errors, ["Le flux est terminé."])
        self.assertEqual(len(self.store.steps), 1)

    def test_portal_revocation_is_noticed_while_paused(self):
        with patch("stepwatch.engine.PORTAL_POLL_INTERVAL", .05):
            recorder = self.make_portal_recorder()
            self.backend.selection_done.set()
            recorder.start()
            self.wait_for(lambda: len(self.saved_events()) == 1)
            recorder.pause()
            self.backend.stream_ended = True
            self.wait_for(lambda: recorder.state == "error")
        self.assertTrue(self.backend.closed)
        self.assertTrue(any("partage de la source a été arrêté" in event["message"]
                            for event in self.observed_events if event["type"] == "error"))

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

    def test_pause_resume_during_locator_does_not_revive_pre_pause_click(self):
        entered, release = threading.Event(), threading.Event()
        real_locate = self.backend.locate_click

        def blocked_locate(target, x, y):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test locator was not released")
            return real_locate(target, x, y)

        self.backend.locate_click = blocked_locate
        self.make_recorder(mode="clicks", click_delay=0)
        listener = self.start()
        hook_thread = threading.Thread(target=listener.click)
        self.addCleanup(hook_thread.join, 3)
        self.addCleanup(release.set)
        hook_thread.start()
        self.assertTrue(entered.wait(2))
        self.recorder.pause()
        self.recorder.resume()
        release.set()
        hook_thread.join(2)
        self.assertFalse(hook_thread.is_alive())
        listener.click(x=0)
        self.wait_for(lambda: any(step["click"] == [20, 10] for step in self.store.steps))
        self.assertTrue(self.recorder.stop())
        self.assertEqual([step["click"] for step in self.store.steps], [[20, 10]])

    def test_stop_during_locator_does_not_enqueue_or_save_click(self):
        entered, release = threading.Event(), threading.Event()
        real_locate = self.backend.locate_click

        def blocked_locate(target, x, y):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test locator was not released")
            return real_locate(target, x, y)

        self.backend.locate_click = blocked_locate
        self.make_recorder(mode="clicks", click_delay=0)
        listener = self.start()
        hook_thread = threading.Thread(target=listener.click)
        self.addCleanup(hook_thread.join, 3)
        self.addCleanup(release.set)
        hook_thread.start()
        self.assertTrue(entered.wait(2))
        self.recorder.stop(timeout=0)
        release.set()
        hook_thread.join(2)
        self.assertFalse(hook_thread.is_alive())
        self.assertTrue(self.recorder.stop())
        self.assertTrue(self.recorder._clicks.empty())
        self.assertEqual(self.store.steps, [])

    def test_debounce_does_not_drop_same_position_click_after_resume(self):
        self.make_recorder(mode="clicks", click_delay=.1)
        listener = self.start()
        instant = time.monotonic()
        with self.recorder._lock, patch("stepwatch.engine.time.monotonic", return_value=instant):
            listener.click()
            self.recorder.pause()
            self.recorder.resume()
            listener.click()
        self.wait_for(lambda: len(self.store.steps) == 1)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.store.steps[0]["click_count"], 1)

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

    def test_unavailable_default_backend_reports_display_problem_and_closes_store(self):
        self.store = SessionStore(self.base_dir, "Intervention de test")
        target = CaptureTarget("region", "Zone de test", Rect(0, 0, 80, 60))
        with patch("stepwatch.engine.capture_backend_factory", return_value=None), \
                patch("stepwatch.engine.display_problem", return_value="Aucun affichage graphique."):
            self.recorder = Recorder(target, CaptureConfig(mode="visual"), self.store)
            self.addCleanup(self.shutdown)
            with patch.object(self.store, "close", wraps=self.store.close) as close:
                self.recorder.start()
                self.wait_for(lambda: self.recorder.state == "error")
                close.assert_called_once_with()
        self.assertTrue(any("Aucun affichage graphique." in event["message"]
                            for event in self.observed_events if event["type"] == "error"))

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

    def test_transient_report_lock_does_not_end_the_recording(self):
        self.make_recorder(mode="visual")
        real_flush = self.store._flush
        failures = {"left": 1}

        def flaky_flush():
            if failures["left"]:
                failures["left"] -= 1
                raise PermissionError(13, "fichier verrouillé par l’antivirus")
            real_flush()

        with patch.object(self.store, "_flush", side_effect=flaky_flush):
            self.recorder.start()
            self.wait_for(lambda: len(self.saved_events()) == 1)
            self.backend.image = Image.new("RGB", (80, 60), "white")
            self.wait_for(lambda: len(self.saved_events()) == 2)
            self.assertTrue(self.recorder.stop())
        self.assertEqual(self.recorder.state, "stopped")
        messages = [event["message"] for event in self.observed_events]
        self.assertEqual(sum("rapport non mis à jour" in message for message in messages), 1)
        self.assertIn("Rapport de nouveau à jour.", messages)
        metadata = json.loads((self.store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual([step["index"] for step in metadata["steps"]], [1, 2])

    def test_fatal_error_traceback_goes_to_the_diagnostic_log(self):
        self.make_recorder(mode="visual")
        with self.assertLogs("stepwatch.engine", level="ERROR") as logs, \
                patch.object(self.store, "save", side_effect=StorageError("Disque plein")):
            self.recorder.start()
            self.wait_for(lambda: self.recorder.state == "error")
            self.assertTrue(self.recorder.stop())
        record = next(record for record in logs.records if record.exc_info)
        self.assertEqual(record.getMessage(), "Enregistrement interrompu")
        self.assertIn("Disque plein", str(record.exc_info[1]))

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

    def test_capture_overlaps_a_blocked_writer_and_stop_drains_only_submitted_frames(self):
        self.make_recorder(mode="clicks", click_delay=0)
        writing, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real_save = self.store.save
        writes = []

        def slow_save(*args, **kwargs):
            writes.append(threading.get_ident())
            writing.set()
            if not release.wait(3):
                raise RuntimeError("Test writer was not released")
            return real_save(*args, **kwargs)

        with patch.object(self.store, "save", side_effect=slow_save):
            listener = self.start()
            listener.click(x=-10)
            self.assertTrue(writing.wait(2))
            listener.click(x=4)
            self.wait_for(lambda: len(self.backend.grab_times) == 2)
            self.assertNotEqual(self.backend.owner, writes[0])
            self.assertEqual(len(writes), 1)  # A second image was acquired without another submitted write.
            self.assertFalse(self.recorder.stop(timeout=.01))
            release.set()
            self.assertTrue(self.recorder.stop())
        self.assertEqual([step["click"] for step in self.store.steps], [[10, 10]])
        self.assertEqual(len(self.saved_events()), 1)

    def test_stop_without_start_writes_valid_empty_report(self):
        self.make_recorder()
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.recorder.state, "stopped")
        metadata = json.loads((self.store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["steps"], [])
        self.assertEqual(self.listeners, [])
        self.assertFalse(self.backend.closed)

    def test_saved_event_carries_bounded_preview_and_png_stays_full_size(self):
        self.backend.rectangle = Rect(0, 0, 3200, 2000)
        self.backend.image = Image.new("RGB", (3200, 2000), "white")
        self.make_recorder(mode="visual")
        self.recorder.start()
        self.wait_for(lambda: len(self.store.steps) == 1)
        self.assertTrue(self.recorder.stop())
        previews = []
        while not self.recorder.events.empty():
            event = self.recorder.events.get_nowait()
            if event["type"] == "saved":
                previews.append(event["image"].size)
        self.assertEqual(previews, [(1600, 1000)])
        with Image.open(self.store.directory / self.store.steps[0]["image"]) as saved:
            self.assertEqual(saved.size, (3200, 2000))

    def test_final_status_is_queued_before_final_state(self):
        self.make_recorder(mode="visual")
        states_at_final_status = []
        emit = self.recorder._emit

        def spy(kind, message, **extra):
            if kind == "status" and "arrêté" in message:
                states_at_final_status.append(self.recorder.state)
            emit(kind, message, **extra)

        self.recorder._emit = spy
        self.recorder.start()
        self.wait_for(lambda: len(self.store.steps) == 1)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(states_at_final_status, ["stopping"])
        self.assertEqual(self.recorder.state, "stopped")

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


class ButtonBackend(FakeBackend):
    """Exposes the physical button probe used by the hook watchdog."""

    def __init__(self):
        super().__init__()
        self.button_down = False

    def mouse_button_down(self):
        return self.button_down


class HookWatchdogTests(unittest.TestCase):
    """Windows can remove a timed-out low-level hook without ending its thread."""

    # Reuse the recorder fixtures without inheriting (and re-running) its tests.
    make_recorder = RecorderTests.make_recorder
    shutdown = RecorderTests.shutdown
    wait_for = RecorderTests.wait_for
    drain_events = RecorderTests.drain_events
    start = RecorderTests.start
    saved_events = RecorderTests.saved_events

    def setUp(self):
        RecorderTests.setUp(self)
        self.backend = ButtonBackend()

    def press_without_hook(self):
        self.backend.button_down = True
        time.sleep(.08)  # Several worker polls see the physical press.
        self.backend.button_down = False

    def test_unreported_press_reinstalls_the_hook(self):
        self.make_recorder(mode="clicks")
        with patch("stepwatch.engine.HOOK_SILENCE_GRACE", .2):
            self.recorder.start()
            self.wait_for(lambda: bool(self.listeners) and self.listeners[0].started.is_set())
            self.press_without_hook()
            self.wait_for(lambda: len(self.listeners) == 2, "Le hook n'a pas été réinstallé")
        old, new = self.listeners
        self.assertEqual(old.stops, 1)
        self.assertTrue(new.started.is_set())
        self.assertEqual(self.recorder.hook_restarts, 1)
        errors = [event for event in self.observed_events if event["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("relancée", errors[0]["message"])
        # The new hook is used for the following clicks.
        new.click()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertTrue(self.recorder.stop())
        self.assertEqual(self.recorder.state, "stopped")
        self.assertEqual(new.stops, 1)

    def test_reported_press_keeps_the_hook(self):
        self.make_recorder(mode="clicks")
        with patch("stepwatch.engine.HOOK_SILENCE_GRACE", .2):
            listener = self.start()
            self.backend.button_down = True
            listener.click()
            time.sleep(.08)
            listener.click(pressed=False)
            self.backend.button_down = False
            time.sleep(.4)
        self.drain_events()
        self.assertEqual(len(self.listeners), 1)
        self.assertEqual(self.recorder.hook_restarts, 0)
        self.assertTrue(self.recorder.stop())

    def test_paused_recording_still_proves_the_hook_alive(self):
        self.make_recorder(mode="clicks")
        with patch("stepwatch.engine.HOOK_SILENCE_GRACE", .2):
            listener = self.start()
            self.recorder.pause()
            self.backend.button_down = True
            listener.click()
            time.sleep(.08)
            self.backend.button_down = False
            time.sleep(.4)
        self.assertEqual(len(self.listeners), 1)
        self.assertTrue(self.recorder.stop())

    def test_visual_mode_has_no_hook_to_watch(self):
        self.make_recorder(mode="visual")
        with patch("stepwatch.engine.HOOK_SILENCE_GRACE", .1):
            self.recorder.start()
            self.press_without_hook()
            time.sleep(.3)
        self.assertEqual(self.listeners, [])
        self.assertTrue(self.recorder.stop())


class NoteTests(unittest.TestCase):
    make_recorder = RecorderTests.make_recorder
    shutdown = RecorderTests.shutdown
    wait_for = RecorderTests.wait_for
    drain_events = RecorderTests.drain_events
    start = RecorderTests.start
    saved_events = RecorderTests.saved_events

    def setUp(self):
        RecorderTests.setUp(self)

    def test_note_is_written_by_the_worker_on_the_latest_step(self):
        self.make_recorder(mode="clicks", click_delay=0)
        listener = self.start()
        self.recorder.add_note("avant toute capture")
        self.wait_for(lambda: any(e["type"] == "error" and "Aucune capture" in e["message"]
                                  for e in self.observed_events))
        listener.click()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.recorder.pause()
        self.recorder.add_note("Pare-feu désactivé")
        self.wait_for(lambda: any(e["type"] == "note" for e in self.observed_events))
        self.assertIn("Note : Pare-feu désactivé", self.store.steps[0]["annotation"])
        self.assertTrue(self.recorder.stop())

    def test_note_typed_just_before_stop_is_kept(self):
        self.make_recorder(mode="clicks", click_delay=0)
        listener = self.start()
        listener.click()
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.recorder.add_note("dernière remarque")
        self.assertTrue(self.recorder.stop())
        data = json.loads((self.store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertIn("Note : dernière remarque", data["steps"][0]["annotation"])

    def test_notes_are_refused_outside_a_recording(self):
        self.make_recorder(mode="clicks")
        with self.assertRaises(RuntimeError):
            self.recorder.add_note("trop tôt")
        self.start()
        with self.assertRaises(ValueError):
            self.recorder.add_note("  ")
        self.assertTrue(self.recorder.stop())
        with self.assertRaises(RuntimeError):
            self.recorder.add_note("trop tard")


class ExcludedRegionTests(unittest.TestCase):
    make_recorder = RecorderTests.make_recorder
    shutdown = RecorderTests.shutdown
    wait_for = RecorderTests.wait_for
    drain_events = RecorderTests.drain_events
    start = RecorderTests.start
    saved_events = RecorderTests.saved_events

    def setUp(self):
        RecorderTests.setUp(self)

    def test_clicks_on_stepwatch_windows_never_become_steps(self):
        self.make_recorder(mode="clicks", click_delay=0)
        listener = self.start()
        self.recorder.set_excluded_regions([Rect(-15, 15, 10, 10)])
        listener.click(-10, 20)  # Inside the target, but on the floating bar.
        time.sleep(.15)
        self.assertEqual(self.saved_events(), [])
        listener.click(30, 40)
        self.wait_for(lambda: len(self.saved_events()) == 1)
        self.assertTrue(self.recorder.stop())

    def test_masked_regions_are_converted_to_target_coordinates(self):
        self.make_recorder()
        target = Rect(100, 50, 400, 300)
        self.recorder.set_excluded_regions([Rect(0, 0, 10, 10)], [Rect(450, 300, 200, 100), Rect(0, 0, 10, 10)])
        self.assertEqual(self.recorder._excluded_masks(target), (Rect(350, 250, 50, 50),))
        self.recorder.set_excluded_regions([Rect(450, 300, 200, 100)])
        self.assertEqual(self.recorder._excluded_masks(target), ())  # Clicks only, no mask.

    def test_masked_regions_are_black_in_captures(self):
        self.backend.image = Image.new("RGB", (80, 60), "white")
        self.make_recorder(mode="visual", interval=.1)
        rect = self.backend.rectangle
        self.recorder.set_excluded_regions([Rect(rect.left, rect.top, 10, 10)],
                                           [Rect(rect.left, rect.top, 10, 10)])
        self.start()
        self.wait_for(lambda: len(self.saved_events()) >= 1)
        self.assertTrue(self.recorder.stop())
        with Image.open(self.store.directory / self.store.steps[0]["image"]) as image:
            self.assertEqual(image.getpixel((2, 2)), (0, 0, 0))
            self.assertEqual(image.getpixel((40, 40)), (255, 255, 255))
