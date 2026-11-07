"""GUI integration checks on a real Tk display, with simulated Windows APIs.

Opt in with STEPWATCH_GUI_TESTS=1 and DISPLAY (for example Xorg/Xvfb).
These tests never claim to exercise actual Windows hooks or capture.
"""

import os
import json
from pathlib import Path
import queue
import re
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageTk

from stepwatch.models import CaptureTarget, Rect
from stepwatch.storage import ReportWriteError, SessionStore, StorageError
from stepwatch.ui import StepWatchApp, run_app


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

    def test_invalid_numbers_name_their_field_in_french(self):
        self.app.interval.set("abc")
        with self.assertRaisesRegex(ValueError, "^Intervalle de comparaison : nombre attendu"):
            self.app._read_config()
        self.app.interval.set("1")
        self.app.masks_text.insert("1.0", "1, 2, 3, 4\n10, 20, x, 40")
        with self.assertRaisesRegex(ValueError, "^Masque ligne 2 : nombre entier"):
            self.app._read_config()
        self.app.masks_text.delete("1.0", "end")
        self.app.masks_text.insert("1.0", "10, 20, 0, 40")
        with self.assertRaisesRegex(ValueError, "^Masque ligne 1 : La largeur"):
            self.app._read_config()
        self.app.masks_text.delete("1.0", "end")
        self.app.source.set("Zone personnalisée")
        self.app._refresh_targets()
        self.app.region_values[2].set("large")
        with self.assertRaisesRegex(ValueError, "^Largeur de la zone : nombre entier"):
            self.app._read_config()

    def test_relative_output_folder_is_rejected_before_any_file_is_written(self):
        self.app.output.set("rapports")
        with patch("stepwatch.ui.sys.platform", "win32"), \
                patch("stepwatch.ui.messagebox.showerror") as error:
            self.app._start()
        self.assertIn("chemin complet", error.call_args.args[1])
        self.assertIsNone(self.app._recorder)
        self.assertFalse(Path("rapports").exists())

    def test_window_fits_a_1366x768_screen(self):
        with patch.object(self.root, "winfo_screenwidth", return_value=1366), \
                patch.object(self.root, "winfo_screenheight", return_value=768):
            self.app._fit_to_screen()
        self.root.update()
        width, height, x, y = map(int, re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)",
                                                self.root.geometry()).groups())
        self.assertLessEqual(x + width, 1366)
        self.assertLessEqual(y + height, 768 - 100)
        min_width, min_height = self.root.minsize()
        self.assertLessEqual(min_width, 1366)
        self.assertLessEqual(min_height, 768 - 100)

    def test_pixel_dimensions_follow_dpi(self):
        top = tk.Toplevel(self.root)
        with patch.object(top, "winfo_fpixels", return_value=144.0), \
                patch("stepwatch.ui.WindowsBackend", return_value=self.backend):
            app = StepWatchApp(top)
        try:
            self.assertEqual(app._px(420), 630)
            canvases = []
            pending = [top]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if isinstance(widget, tk.Canvas):
                    canvases.append(int(widget["width"]))
            self.assertIn(630, canvases)
            expected = min(app._px(1020), top.winfo_screenwidth() - app._px(40))
            self.assertEqual(top.minsize()[0], expected)
        finally:
            top.after_cancel(app._poll_id)
            top.destroy()

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

    def test_failing_event_is_logged_and_polling_continues(self):
        recorder = Mock(state="recording", events=queue.Queue())
        self.app._recorder = recorder
        recorder.events.put({"type": "saved", "image": object(), "message": "x", "kept": 1, "ignored": 0})
        recorder.events.put({"type": "log", "message": "Événement suivant", "kept": 2, "ignored": 4})
        self.app._poll()
        self.assertTrue(recorder.events.empty())
        self.assertEqual(self.app.kept.get(), "2")
        self.assertIn("Erreur d’affichage ignorée", self.app.log.get("1.0", "end"))
        self.assertIn("Événement suivant", self.app.log.get("1.0", "end"))
        self.assertIsNotNone(self.app._poll_id)
        self.app._recorder = None

    def test_direct_poll_calls_keep_a_single_polling_loop(self):
        self.app._poll()
        self.app._poll()
        scripts = [self.root.tk.splitlist(self.root.tk.call("after", "info", event))[0]
                   for event in self.root.tk.splitlist(self.root.tk.call("after", "info"))]
        self.assertEqual(sum(script.endswith("_poll") for script in scripts), 1)

    def test_preview_requested_size_does_not_follow_the_image(self):
        self.root.geometry("1190x820")
        self.root.update()
        renders = []
        real_photo = ImageTk.PhotoImage

        def photo(*args, **kwargs):
            renders.append(args[0].size)
            return real_photo(*args, **kwargs)

        with patch("stepwatch.ui.ImageTk.PhotoImage", photo):
            self.app._handle_event({"type": "saved", "image": Image.new("RGB", (1600, 900), "#2868a1"),
                                    "kept": 1, "ignored": 0, "message": "Capture"})
            for _ in range(10):
                self.root.update()
        self.assertLessEqual(self.app.preview.winfo_reqheight(), 20)
        self.assertLessEqual(len(renders), 2)
        width, height = renders[-1]
        self.assertLessEqual(width, self.app.preview.winfo_width())
        self.assertLessEqual(height, self.app.preview.winfo_height())

    def test_start_shows_events_left_by_previous_recording(self):
        previous = Mock(state="stopped", events=queue.Queue())
        previous.events.put({"type": "error", "message": "Erreur d’écriture du rapport : verrou",
                             "kept": 3, "ignored": 0})
        self.app._recorder = previous
        started = []

        class StartedRecorder:
            def __init__(self, target, config, store):
                self.state, self.events, self.store = "recording", queue.Queue(), store
                started.append(self)

            def start(self):
                pass

            def stop(self, timeout=5):
                self.state = "stopped"
                self.store.close()
                return True

        with patch("stepwatch.ui.sys.platform", "win32"), patch("stepwatch.ui.Recorder", StartedRecorder):
            self.app._start()
        self.assertTrue(previous.events.empty())
        self.assertIn("Erreur d’écriture du rapport", self.app.log.get("1.0", "end"))
        self.assertEqual(len(started), 1)
        self.assertEqual(self.app.kept.get(), "0")
        started[0].stop()
        self.app._recorder = None

    def test_standalone_export_runs_in_background_and_is_logged(self):
        self.assertEqual(str(self.app.export_button["state"]), "disabled")
        store = SessionStore(Path(self.temp.name), "Export")
        store.save(Image.new("RGB", (8, 8), "red"), "initial")
        store.close()
        self.app._session_dir = store.directory
        self.app._update_controls()
        self.assertEqual(str(self.app.export_button["state"]), "normal")
        self.app._export_standalone()
        self.app._export_thread.join(5)
        self.app._poll()
        self.assertIn("Rapport autonome :", self.app.log.get("1.0", "end"))
        self.assertTrue((store.directory / "rapport-autonome.html").is_file())

    def test_export_retries_current_sessions_failed_final_report_flush(self):
        store = SessionStore(Path(self.temp.name), "Rapport en retard")
        store.save(Image.new("RGB", (8, 8), "red"), "initial")
        with patch.object(store, "_flush", side_effect=PermissionError("rapport verrouillé")):
            with self.assertRaises(ReportWriteError):
                store.save(Image.new("RGB", (8, 8), "blue"), "visual_change")
            with self.assertRaises(StorageError):
                store.close()
        self.assertEqual(len(json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))["steps"]), 1)
        self.app._session_dir = store.directory
        self.app._recorder = Mock(state="error", events=queue.Queue(), store=store)
        with patch.object(store, "close", wraps=store.close) as flush:
            self.app._export_standalone()
            self.app._export_thread.join(5)
        flush.assert_called_once()
        self.assertFalse(self.app._export_thread.is_alive())
        report = (store.directory / "rapport-autonome.html").read_text(encoding="utf-8")
        self.assertEqual(report.count('src="data:image/png;base64,'), 2)
        self.assertEqual(len(json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))["steps"]), 2)
        self.app._recorder = None

    def test_export_does_not_publish_stale_steps_when_report_is_still_locked(self):
        store = SessionStore(Path(self.temp.name), "Verrou persistant")
        self.app._session_dir = store.directory
        self.app._recorder = Mock(state="error", events=queue.Queue(), store=store)
        with patch.object(store, "close", side_effect=StorageError("rapport encore verrouillé")), \
                patch("stepwatch.ui.export_standalone") as export:
            self.app._export_standalone()
            self.app._export_thread.join(5)
        export.assert_not_called()
        event = self.app._background_events.get_nowait()
        self.assertEqual(event["type"], "error")
        self.assertIn("rapport encore verrouillé", event["message"])
        self.assertFalse((store.directory / "rapport-autonome.html").exists())
        self.app._recorder = None

    def test_export_of_import_does_not_flush_a_different_previous_recorder(self):
        previous = SessionStore(Path(self.temp.name), "Précédente")
        imported = SessionStore(Path(self.temp.name), "Import ShareX")
        imported.save(Image.new("RGB", (8, 8), "red"), "import_sharex")
        self.app._session_dir = imported.directory
        self.app._recorder = Mock(state="stopped", events=queue.Queue(), store=previous)
        with patch.object(previous, "close") as flush:
            self.app._export_standalone()
            self.app._export_thread.join(5)
        flush.assert_not_called()
        self.assertTrue((imported.directory / "rapport-autonome.html").exists())
        self.app._recorder = None

    def test_start_during_background_work_does_not_replace_the_session(self):
        self.app._export_thread = Mock()
        self.app._export_thread.is_alive.return_value = True
        with patch("stepwatch.ui.SessionStore") as store:
            self.app._start()
        store.assert_not_called()
        self.assertIsNone(self.app._recorder)
        self.app._export_thread = None

    def test_non_windows_start_is_explicit_and_leaves_no_session(self):
        with patch("stepwatch.ui.sys.platform", "linux"), patch("stepwatch.ui.messagebox.showerror") as error:
            self.app._start()
        error.assert_called_once()
        self.assertIn("nécessite Windows", error.call_args.args[1])
        self.assertIsNone(self.app._recorder)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_run_app_stops_recording_when_tk_loop_is_interrupted(self):
        recorder = Mock(state="recording", events=queue.Queue())
        self.app._recorder = recorder
        with patch("stepwatch.ui.tk.Tk", return_value=self.root), \
                patch("stepwatch.ui.StepWatchApp", return_value=self.app), \
                patch.object(self.root, "mainloop", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_app()
        recorder.stop.assert_called_once()
        self.app._recorder = None

    def test_tk_callback_errors_reach_the_journal_and_the_diagnostic_log(self):
        with self.assertLogs("stepwatch.ui", level="ERROR") as logs:
            try:
                raise ValueError("rappel cassé")
            except ValueError as exc:
                self.root.report_callback_exception(type(exc), exc, exc.__traceback__)
        self.assertIn("Erreur interne : rappel cassé", self.app.log.get("1.0", "end"))
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_journal_is_mirrored_without_flooding_ignored_comparisons(self):
        with self.assertLogs("stepwatch.ui", level="DEBUG") as logs:
            self.app._handle_event({"type": "ignored", "message": "Variation visuelle ignorée (0.00 %).",
                                    "kept": 0, "ignored": 1})
            self.app._handle_event({"type": "error", "message": "La fenêtre sélectionnée est réduite.",
                                    "kept": 0, "ignored": 1})
            self.app._handle_event({"type": "saved", "message": "Capture enregistrée.", "kept": 1,
                                    "ignored": 1})
        levels = {record.getMessage(): record.levelname for record in logs.records}
        self.assertEqual(levels, {"Variation visuelle ignorée (0.00 %).": "DEBUG",
                                  "La fenêtre sélectionnée est réduite.": "WARNING",
                                  "Capture enregistrée.": "INFO"})

    def test_diagnostic_log_location_is_shown_at_startup(self):
        top = tk.Toplevel(self.root)
        with patch("stepwatch.ui.WindowsBackend", return_value=self.backend):
            app = StepWatchApp(top, Path("/journal/stepwatch.log"))
        try:
            self.assertIn("Journal de diagnostic : ", app.log.get("1.0", "end"))
        finally:
            top.after_cancel(app._poll_id)
            top.destroy()

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


class ShutdownTests(unittest.TestCase):
    """Failure paths that do not need a display or actual Windows resources."""

    def setUp(self):
        self.app = StepWatchApp.__new__(StepWatchApp)
        self.app.root = Mock()
        self.app._recorder = Mock(state="stopping")
        self.app._write_log = Mock()
        self.app._update_controls = Mock()
        self.app._background_running = Mock(return_value=False)
        self.app._backend = Mock()
        self.app._poll_id = None
        self.app._close_error = None
        self.app._closing = True

    def test_finish_close_retries_after_exception_and_waits_for_real_completion(self):
        self.app._recorder.stop.side_effect = (OSError("verrou passager"), False, True)
        with self.assertLogs("stepwatch.ui", level="ERROR") as logs:
            self.app._finish_close()
        self.assertIn("Fermeture incomplète", logs.output[0])
        self.app.root.destroy.assert_not_called()
        self.assertEqual(self.app.root.after.call_count, 1)
        self.app._finish_close()
        self.app.root.destroy.assert_not_called()
        self.assertEqual(self.app.root.after.call_count, 2)
        self.app._finish_close()
        self.app.root.destroy.assert_called_once()
        self.app._backend.close.assert_called_once()
        self.assertEqual(self.app.root.after.call_count, 2)

    def test_shutdown_timeout_is_reported_without_claiming_success(self):
        self.app._recorder.stop.return_value = False
        with self.assertLogs("stepwatch.ui", level="WARNING") as logs:
            self.assertFalse(self.app.shutdown(timeout=.01))
        self.assertIn("thread de capture est encore actif", logs.output[0])
        self.app._recorder.stop.assert_called_once_with(timeout=.01)
        self.app.root.destroy.assert_not_called()

    def test_shutdown_exception_is_logged_with_traceback(self):
        self.app._recorder.stop.side_effect = OSError("écriture impossible")
        with self.assertLogs("stepwatch.ui", level="ERROR") as logs:
            self.assertFalse(self.app.shutdown(timeout=.01))
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertIn("écriture impossible", logs.output[0])


if __name__ == "__main__":
    unittest.main()
