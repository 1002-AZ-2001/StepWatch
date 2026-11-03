"""GUI integration checks on a real Tk display, with simulated Windows APIs.

Opt in with STEPWATCH_GUI_TESTS=1 and DISPLAY (for example Xorg/Xvfb).
These tests never claim to exercise actual Windows hooks or capture.
"""

import os
from pathlib import Path
import queue
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from stepwatch.models import CaptureTarget, Rect
from stepwatch.ui import StepWatchApp


@unittest.skipUnless(
    os.environ.get("STEPWATCH_GUI_TESTS") == "1" and os.environ.get("DISPLAY"),
    "Interface : activer STEPWATCH_GUI_TESTS=1 avec un affichage DISPLAY.",
)
class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.backend = Mock()
        self.backend.list_windows.return_value = [
            CaptureTarget("window", "Bureau distant — Assistance", hwnd=123)
        ]
        self.backend.list_monitors.return_value = [
            CaptureTarget("monitor", "Écran secondaire", rect=Rect(-1920, 0, 1920, 1080))
        ]
        self.backend.resolve.return_value = Rect(0, 0, 1280, 720)
        with patch("stepwatch.ui.WindowsBackend", return_value=self.backend):
            self.app = StepWatchApp(self.root)
        self.app.output.set(self.temp.name)
        self.root.update()

    def tearDown(self):
        try:
            if self.root.winfo_exists():
                if self.app._poll_id:
                    self.root.after_cancel(self.app._poll_id)
                self.root.destroy()
        except tk.TclError:
            pass
        self.temp.cleanup()

    def test_constructor_does_not_record(self):
        self.assertIsNone(self.app._recorder)
        self.assertEqual(self.app.state_text.get(), "Prêt")
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertIn("Bureau distant", self.app.target.get())
        self.backend.list_windows.assert_called_once()
        self.backend.list_monitors.assert_not_called()
        self.assertEqual(str(self.app.pause_button["state"]), "disabled")

    def test_custom_region_masks_and_french_decimal_settings(self):
        self.app.source.set("Zone personnalisée")
        self.app._refresh_targets()
        for var, value in zip(self.app.region_values, ("-1920", "-100", "1000", "700")):
            var.set(value)
        self.app.interval.set("1,5")
        self.app.delay.set("0,25")
        self.app.sensitivity.set("0,4")
        self.app.conservative.set(True)
        self.app.masks_text.insert("1.0", "10, 20, 30, 40\n50; 60; 70; 80\n")
        target, config = self.app._read_config()
        self.assertEqual(target.rect, Rect(-1920, -100, 1000, 700))
        self.assertEqual(config.interval, 1.5)
        self.assertEqual(config.click_delay, .25)
        self.assertEqual(config.sensitivity, .4)
        self.assertTrue(config.conservative)
        self.assertEqual(config.masks, (Rect(10, 20, 30, 40), Rect(50, 60, 70, 80)))

    def test_invalid_mask_and_missing_target_are_actionable(self):
        self.app.masks_text.insert("1.0", "10, 20, 30")
        with self.assertRaisesRegex(ValueError, "Masque ligne 1"):
            self.app._read_config()
        self.app.masks_text.delete("1.0", "end")
        self.app.target.set("Cible qui n’existe pas")
        with self.assertRaisesRegex(ValueError, "Sélectionnez"):
            self.app._read_config()

    def test_preview_and_counters_update_on_main_thread(self):
        self.app._handle_event({
            "type": "saved", "image": Image.new("RGB", (640, 360), "#2868a1"),
            "kept": 7, "ignored": 3, "message": "Capture de test conservée.",
        })
        self.root.update()
        self.assertEqual(self.app.kept.get(), "7")
        self.assertEqual(self.app.ignored.get(), "3")
        self.assertIsNotNone(self.app._photo)
        self.assertTrue(self.app.preview["image"])
        self.assertIn("Capture de test", self.app.log.get("1.0", "end"))

    def test_non_windows_start_is_explicit_and_leaves_no_session(self):
        with patch("stepwatch.ui.sys.platform", "linux"), patch("stepwatch.ui.messagebox.showerror") as error:
            self.app._start()
        error.assert_called_once()
        self.assertIn("nécessite Windows", error.call_args.args[1])
        self.assertIsNone(self.app._recorder)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_recorder_controls_and_graceful_close(self):
        class FakeRecorder:
            state = "idle"

            def __init__(self):
                self.events = queue.Queue()
                self.stop_calls = []

            def pause(self):
                self.state = "paused"

            def resume(self):
                self.state = "recording"

            def stop(self, timeout=5):
                self.stop_calls.append(timeout)
                self.state = "stopped"
                return True

        recorder = FakeRecorder()
        recorder.state = "recording"
        self.app._recorder = recorder
        self.app._update_controls()
        self.assertEqual(str(self.app.start_button["state"]), "disabled")
        self.app._pause()
        self.assertEqual(recorder.state, "paused")
        self.assertEqual(str(self.app.resume_button["state"]), "normal")
        self.app._resume()
        self.assertEqual(recorder.state, "recording")
        with patch("stepwatch.ui.messagebox.askyesno", return_value=True):
            self.app._close()
        self.assertTrue(recorder.stop_calls)
        self.assertTrue(all(timeout == 0 for timeout in recorder.stop_calls))
        self.backend.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
