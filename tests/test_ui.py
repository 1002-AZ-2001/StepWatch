"""Fenêtre principale (Qt) avec un moteur de capture simulé.

Ces tests n'exercent jamais les API de capture réelles.
"""

from concurrent.futures import Future
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import qt_helpers  # noqa: F401 - plateforme Qt hors écran, avant toute fenêtre
from qt_helpers import close_app, default_backend, log_text, make_app, pump

from PIL import Image
from PySide6.QtCore import QRect

from stepwatch.models import CaptureTarget, Rect
from stepwatch.review import PAGE_SIZE, SessionReview
from stepwatch.storage import ReportWriteError, SessionStore, StorageError
from stepwatch.ui import StepWatchApp, _log_unhandled, run_app


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = default_backend()
        self.backend.list_windows.return_value = [CaptureTarget("window", "Bureau distant — Assistance", hwnd=123)]
        self.backend.list_monitors.return_value = [
            CaptureTarget("monitor", "Écran secondaire", rect=Rect(-1920, 0, 1920, 1080))
        ]
        self.backend.resolve.return_value = Rect(0, 0, 1280, 720)
        self.backend_factory = Mock(return_value=self.backend)
        self.app = make_app(factory=self.backend_factory)
        self.app.output.set(self.temp.name)

    def tearDown(self):
        app = self.app
        errors = []
        try:
            if app._recorder is not None:
                try:
                    if not app._recorder.stop(timeout=5):
                        errors.append("Le thread de capture n'est pas arrêté.")
                except Exception as exc:
                    errors.append(f"Arrêt du moteur : {exc}")
                app._recorder = None
            for name in ("_import_thread", "_export_thread"):
                worker = getattr(app, name)
                if isinstance(worker, threading.Thread):
                    worker.join(timeout=5)
                    if worker.is_alive():
                        errors.append(f"Thread {name} encore actif.")
            close_app(app)
        finally:
            self.app = self.backend = None
            self.temp.cleanup()
        self.assertEqual(errors, [])

    def test_constructor_does_not_record(self):
        self.assertIsNone(self.app._recorder)
        self.assertEqual(self.app.state_text.get(), "Prêt")
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertIn("Bureau distant", self.app.target_text())
        self.backend.list_windows.assert_called_once()
        self.backend.list_monitors.assert_not_called()
        self.assertFalse(self.app.pause_button.isEnabled())

    def test_refresh_keeps_window_identity_when_titles_match(self):
        first = CaptureTarget("window", "Assistance", hwnd=101)
        second = CaptureTarget("window", "Assistance", hwnd=202)
        self.backend.list_windows.return_value = [first, second]
        self.app._refresh_targets()
        self.app.target_combo.setCurrentIndex(1)
        self.backend.list_windows.return_value = [second, first]
        self.app._refresh_targets()
        self.assertEqual(self.app._read_config()[0], second)
        self.backend.list_windows.return_value = [first]
        self.app._refresh_targets()
        self.assertEqual(self.app.target_text(), "Sélectionnez une cible")
        self.app._refresh_targets()
        with self.assertRaisesRegex(ValueError, "Sélectionnez une fenêtre"):
            self.app._read_config()

    def test_monitor_source_lists_monitors(self):
        self.app.source.set("Moniteur")
        self.app._refresh_targets()
        self.assertIn("Écran secondaire", self.app.target_text())
        self.assertEqual(self.app._read_config()[0].rect, Rect(-1920, 0, 1920, 1080))

    def test_custom_region_masks_and_french_decimal_settings(self):
        self.app.source.set("Zone personnalisée")
        self.app._refresh_targets()
        self.assertFalse(self.app.region_frame.isHidden())
        for var, value in zip(self.app.region_values, ("-1920", "-100", "1000", "700")):
            var.set(value)
        self.app.interval.set("1,5")
        self.app.delay.set("0,25")
        self.app.sensitivity.set("0,4")
        self.app.conservative.set(True)
        self.app.masks_text.setPlainText("10, 20, 30, 40\n50; 60; 70; 80\n")
        target, config = self.app._read_config()
        self.assertEqual(target.rect, Rect(-1920, -100, 1000, 700))
        self.assertEqual(config.interval, 1.5)
        self.assertEqual(config.click_delay, .25)
        self.assertEqual(config.sensitivity, .4)
        self.assertTrue(config.conservative)
        self.assertEqual(config.masks, (Rect(10, 20, 30, 40), Rect(50, 60, 70, 80)))

    def test_widgets_and_values_stay_in_sync(self):
        line = self.app.region_values[0]
        self.app.interval.set("2,5")
        edits = [widget for widget in self.app._settings_widgets if hasattr(widget, "text")
                 and callable(widget.text) and widget.text() == "2,5"]
        self.assertTrue(edits)
        edits[0].setText("3,0")
        self.assertEqual(self.app.interval.get(), "3,0")
        self.app.conservative_check.setChecked(True)
        self.assertTrue(self.app.conservative.get())
        self.app.mode_combo.setCurrentIndex(1)
        self.assertEqual(self.app.mode.get(), "Clics locaux uniquement")
        self.assertEqual(line.get(), "0")

    def test_invalid_mask_and_missing_target_are_actionable(self):
        self.app.masks_text.setPlainText("10, 20, 30")
        with self.assertRaisesRegex(ValueError, "Masque ligne 1"):
            self.app._read_config()
        self.app.masks_text.setPlainText("")
        self.app.target_combo.setCurrentIndex(-1)
        with self.assertRaisesRegex(ValueError, "Sélectionnez"):
            self.app._read_config()

    def test_invalid_numbers_name_their_field_in_french(self):
        self.app.interval.set("abc")
        with self.assertRaisesRegex(ValueError, "^Intervalle de comparaison : nombre attendu"):
            self.app._read_config()
        self.app.interval.set("1")
        self.app.masks_text.setPlainText("1, 2, 3, 4\n10, 20, x, 40")
        with self.assertRaisesRegex(ValueError, "^Masque ligne 2 : nombre entier"):
            self.app._read_config()
        self.app.masks_text.setPlainText("10, 20, 0, 40")
        with self.assertRaisesRegex(ValueError, "^Masque ligne 1 : La largeur"):
            self.app._read_config()
        self.app.masks_text.setPlainText("")
        self.app.source.set("Zone personnalisée")
        self.app._refresh_targets()
        self.app.region_values[2].set("large")
        with self.assertRaisesRegex(ValueError, "^Largeur de la zone : nombre entier"):
            self.app._read_config()

    def test_relative_output_folder_is_rejected_before_any_file_is_written(self):
        self.app.output.set("rapports")
        with patch("stepwatch.ui.messagebox.showerror") as error:
            self.app._start()
        self.assertIn("chemin complet", error.call_args.args[1])
        self.assertIsNone(self.app._recorder)
        self.assertFalse(Path("rapports").exists())

    def test_window_fits_a_1366x768_screen(self):
        area = QRect(0, 0, 1366, 728)
        self.app._fit_to_screen(area)
        frame = self.app.root.geometry()
        self.assertLessEqual(frame.x() + frame.width(), 1366)
        self.assertLessEqual(frame.y() + frame.height(), 728)
        self.assertLessEqual(self.app.root.minimumWidth(), 1366)
        self.assertLessEqual(self.app.root.minimumHeight(), 728)

    def test_native_rect_converts_scaled_coordinates_to_physical_pixels(self):
        screen = Mock()
        screen.devicePixelRatio.return_value = 1.5
        screen.geometry.return_value = QRect(1920, 0, 1707, 960)
        widget = Mock()
        widget.frameGeometry.return_value = QRect(2020, 100, 400, 200)
        widget.screen.return_value = screen
        self.assertEqual(StepWatchApp._native_rect(widget), Rect(2070, 150, 600, 300))
        widget.frameGeometry.return_value = QRect(0, 0, 1, 1)
        self.assertIsNone(StepWatchApp._native_rect(widget))

    def test_preview_and_counters_update_on_main_thread(self):
        self.app._handle_event({
            "type": "saved", "image": Image.new("RGB", (640, 360), "#2868a1"),
            "kept": 7, "ignored": 3, "message": "Capture de test conservée.",
        })
        pump()
        self.assertEqual(self.app.kept.get(), "7")
        self.assertEqual(self.app.ignored.get(), "3")
        self.assertTrue(self.app.preview.has_image)
        pixmap = self.app.preview.pixmap()
        self.assertFalse(pixmap.isNull())
        self.assertLessEqual(pixmap.width() / pixmap.devicePixelRatio(), self.app.preview.width())
        self.assertIn("Capture de test", log_text(self.app))

    def test_large_preview_is_scaled_to_the_label(self):
        self.app.root.resize(1190, 800)
        pump()
        self.app._handle_event({"type": "saved", "image": Image.new("RGB", (3200, 1800), "red"),
                                "kept": 1, "ignored": 0, "message": "Capture"})
        pump()
        pixmap = self.app.preview.pixmap()
        ratio = pixmap.devicePixelRatio()
        self.assertLessEqual(pixmap.width() / ratio, self.app.preview.width())
        self.assertLessEqual(pixmap.height() / ratio, self.app.preview.height())
        self.assertEqual(pixmap.toImage().pixelColor(5, 5).name(), "#ff0000")

    def test_reopen_session_pages_metadata_and_reads_only_selected_images(self):
        store = SessionStore(Path(self.temp.name), "Guide de test")
        first = store.save(Image.new("RGB", (320, 200), "red"), "initial", annotation="Première étape")
        last = store.save(Image.new("RGB", (320, 200), "blue"), "local_click", annotation="Étape suivante")
        manifest = store.directory / "steps.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["steps"] = [dict(first, index=i + 1) for i in range(PAGE_SIZE)] + [dict(last, index=PAGE_SIZE + 1)]
        manifest.write_text(json.dumps(data), encoding="utf-8")

        def wait_image(color):
            pump(lambda: self.app._review._image is not None and self.app._review._image.getpixel((0, 0)) == color,
                 timeout=4, app=self.app, message="L’aperçu de l’étape sélectionnée n’a pas été chargé.")

        self.app._tabs.setCurrentWidget(self.app._review)
        with patch("stepwatch.ui.filedialog.askdirectory", return_value=str(store.directory)), \
                patch("stepwatch.review.Image.open", wraps=Image.open) as opened:
            self.app._choose_session()
            wait_image((255, 0, 0))
            self.assertEqual(self.app._review.list.topLevelItemCount(), PAGE_SIZE)
            self.assertEqual(self.app._session_dir, store.directory)
            self.assertIn("Première étape", self.app._review.annotation.toPlainText())
            self.app._review.next.click()
            wait_image((0, 0, 255))
            self.assertEqual(self.app._review.list.topLevelItemCount(), 1)
            self.assertEqual(opened.call_count, 2)
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), data)

    def test_failing_event_is_logged_and_polling_continues(self):
        recorder = Mock(state="recording", events=queue.Queue())
        self.app._recorder = recorder
        recorder.events.put({"type": "saved", "image": object(), "message": "x", "kept": 1, "ignored": 0})
        recorder.events.put({"type": "log", "message": "Événement suivant", "kept": 2, "ignored": 4})
        self.app._poll()
        self.assertTrue(recorder.events.empty())
        self.assertEqual(self.app.kept.get(), "2")
        self.assertIn("Erreur d’affichage ignorée", log_text(self.app))
        self.assertIn("Événement suivant", log_text(self.app))
        self.assertTrue(self.app._timer.isActive())
        self.app._recorder = None

    def test_direct_poll_calls_keep_a_single_polling_timer(self):
        self.app._poll()
        self.app._poll()
        self.assertTrue(self.app._timer.isActive())
        self.assertTrue(self.app._timer.isSingleShot())

    def test_start_shows_events_left_by_previous_recording(self):
        previous = Mock(state="stopped", events=queue.Queue())
        previous.events.put({"type": "error", "message": "Erreur d’écriture du rapport : verrou",
                             "kept": 3, "ignored": 0})
        self.app._recorder = previous
        started = []

        class StartedRecorder:
            def __init__(self, target, config, store, *, backend_factory):
                self.state, self.events, self.store = "recording", queue.Queue(), store
                self.backend_factory = backend_factory
                started.append(self)

            def start(self):
                pass

            def stop(self, timeout=5):
                self.state = "stopped"
                self.store.close()
                return True

        self.app.compact.set(False)
        with patch("stepwatch.ui.Recorder", StartedRecorder):
            self.app._start()
        self.assertTrue(previous.events.empty())
        self.assertIn("Erreur d’écriture du rapport", log_text(self.app))
        self.assertEqual(len(started), 1)
        self.assertIs(started[0].backend_factory, self.backend_factory)
        self.assertEqual(self.app.kept.get(), "0")
        started[0].stop()
        self.app._recorder = None

    def test_standalone_export_runs_in_background_and_is_logged(self):
        self.assertFalse(self.app.export_button.isEnabled())
        store = SessionStore(Path(self.temp.name), "Export")
        store.save(Image.new("RGB", (8, 8), "red"), "initial")
        store.close()
        self.app._session_dir = store.directory
        self.app._update_controls()
        self.assertTrue(self.app.export_button.isEnabled())
        self.app._export_standalone()
        self.app._export_thread.join(5)
        self.app._poll()
        self.assertIn("Rapport autonome :", log_text(self.app))
        self.assertTrue((store.directory / "rapport-autonome.html").is_file())
        self.assertIn("Rapport Markdown :", log_text(self.app))
        self.assertTrue((store.directory / "rapport.md").is_file())

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

    def test_unavailable_capture_leaves_import_enabled_and_creates_no_session(self):
        with patch("stepwatch.ui.capture_backend_factory", return_value=None):
            app = StepWatchApp()
        try:
            app.output.set(self.temp.name)
            self.assertFalse(app.start_button.isEnabled())
            self.assertTrue(app.import_button.isEnabled())
            with patch("stepwatch.ui.messagebox.showerror") as error:
                app._start()
            error.assert_called_once()
            self.assertIn("indisponible", error.call_args.args[1])
            self.assertIsNone(app._recorder)
        finally:
            close_app(app)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_portal_selection_leaves_stop_available_and_uses_visual_mode(self):
        backend = Mock(uses_portal=True)
        factory = Mock(return_value=backend)
        app = make_app(factory=factory)
        try:
            app.output.set(self.temp.name)
            app.mode.set("Clics locaux uniquement")
            app.delay.set("champ inutilisé")
            app._show_advanced(True)
            app.masks_text.setPlainText("0,0,40,40")
            recorder = Mock(state="starting", events=queue.Queue())
            with patch("stepwatch.ui.Recorder", return_value=recorder) as recording:
                app._start()
            target, config, _store = recording.call_args.args
            self.assertEqual(target.kind, "portal")
            self.assertEqual(config.mode, "visual")
            self.assertEqual(config.masks, (Rect(0, 0, 40, 40),))
            self.assertIs(recording.call_args.kwargs["backend_factory"], factory)
            backend.list_windows.assert_not_called()
            backend.list_monitors.assert_not_called()
            backend.resolve.assert_not_called()
            self.assertIn("à partager", app.state_text.get())
            self.assertFalse(app.start_button.isEnabled())
            self.assertFalse(app.pause_button.isEnabled())
            self.assertTrue(app.stop_button.isEnabled())
            app._stop()
            recorder.stop.assert_called_once_with(timeout=0)
            app._recorder = None
        finally:
            close_app(app)

    def test_run_app_stops_recording_when_the_event_loop_is_interrupted(self):
        recorder = Mock(state="recording", events=queue.Queue())
        self.app._recorder = recorder
        fake_qapp = Mock()
        fake_qapp.exec.side_effect = KeyboardInterrupt
        with patch("stepwatch.ui.application", return_value=fake_qapp), \
                patch("stepwatch.ui.StepWatchApp", return_value=self.app), \
                patch("stepwatch.ui.Preferences"):
            with self.assertRaises(KeyboardInterrupt):
                run_app()
        recorder.stop.assert_called_once()
        self.assertTrue(self.app._disposed)
        self.app._recorder = None

    def test_unhandled_callback_errors_reach_the_diagnostic_log(self):
        with self.assertLogs("stepwatch.ui", level="ERROR") as logs:
            try:
                raise ValueError("rappel cassé")
            except ValueError as exc:
                _log_unhandled(type(exc), exc, exc.__traceback__)
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
        app = make_app(log_path=Path("/journal/stepwatch.log"))
        try:
            self.assertIn("Journal de diagnostic : ", log_text(app))
        finally:
            close_app(app)

    def test_recorder_controls_and_graceful_close(self):
        class FakeRecorder:
            state = "idle"

            def __init__(self):
                self.events = queue.Queue()
                self.stop_calls = []
                self.notes = []

            def pause(self):
                self.state = "paused"

            def resume(self):
                self.state = "recording"

            def stop(self, timeout=5):
                self.stop_calls.append(timeout)
                self.state = "stopped"
                return True

            def add_note(self, text):
                self.notes.append(text)

        recorder = FakeRecorder()
        self.assertFalse(self.app.note_button.isEnabled())
        recorder.state = "recording"
        self.app._recorder = recorder
        self.app._update_controls()
        self.assertFalse(self.app.start_button.isEnabled())
        self.assertTrue(self.app.note_button.isEnabled())
        with patch("stepwatch.ui.simpledialog.askstring", return_value="Câble réseau débranché"):
            self.app._add_note()
        with patch("stepwatch.ui.simpledialog.askstring", return_value=None):
            self.app._add_note()  # Dialogue annulé : rien n'est envoyé.
        self.assertEqual(recorder.notes, ["Câble réseau débranché"])
        self.app._pause()
        self.assertEqual(recorder.state, "paused")
        self.assertTrue(self.app.resume_button.isEnabled())
        self.app._resume()
        self.assertEqual(recorder.state, "recording")
        with patch("stepwatch.ui.messagebox.askyesno", return_value=True):
            self.app._close()
        self.assertTrue(recorder.stop_calls)
        self.assertTrue(all(timeout == 0 for timeout in recorder.stop_calls))
        self.assertTrue(self.app._disposed)
        self.backend.close.assert_called_once()

    def test_main_window_close_button_goes_through_the_graceful_close(self):
        with patch.object(self.app, "_close") as close:
            self.app.root.close()
            pump(lambda: close.called)
        self.assertFalse(self.app._disposed)


class ReviewShutdownTests(unittest.TestCase):
    def test_finished_io_timeout_does_not_block_shutdown(self):
        finished = Future()
        finished.set_exception(TimeoutError("Lecture en échec"))
        review = SimpleNamespace(_running=(1, "preview", None, finished), _executor=Mock(), _pending=None)

        self.assertTrue(SessionReview.shutdown(review, timeout=0))
        self.assertIsNone(review._running)
        review._executor.shutdown.assert_called_once_with(wait=True)


class ShutdownTests(unittest.TestCase):
    """Chemins d'échec qui n'ont besoin ni d'affichage ni de ressources Windows."""

    def setUp(self):
        self.app = StepWatchApp.__new__(StepWatchApp)
        self.app._recorder = Mock(state="stopping")
        self.app._write_log = Mock()
        self.app._update_controls = Mock()
        self.app._background_running = Mock(return_value=False)
        self.app._review = Mock()
        self.app._review.shutdown.return_value = True
        self.app._import_thread = self.app._export_thread = None
        self.app._close_error = None
        self.app._closing = True
        self.app._disposed = False
        self.app.dispose = Mock()

    def test_finish_close_retries_after_exception_and_waits_for_real_completion(self):
        self.app._recorder.stop.side_effect = (OSError("verrou passager"), False, True)
        with patch("stepwatch.ui.QTimer.singleShot") as retry:
            with self.assertLogs("stepwatch.ui", level="ERROR") as logs:
                self.app._finish_close()
            self.assertIn("Fermeture incomplète", logs.output[0])
            self.app.dispose.assert_not_called()
            self.assertEqual(retry.call_count, 1)
            self.app._finish_close()
            self.app.dispose.assert_not_called()
            self.assertEqual(retry.call_count, 2)
            self.app._finish_close()
            self.app.dispose.assert_called_once()
            self.assertEqual(retry.call_count, 2)

    def test_shutdown_timeout_is_reported_without_claiming_success(self):
        self.app._recorder.stop.return_value = False
        with self.assertLogs("stepwatch.ui", level="WARNING") as logs:
            self.assertFalse(self.app.shutdown(timeout=.01))
        self.assertIn("thread de capture est encore actif", logs.output[0])
        self.app._recorder.stop.assert_called_once_with(timeout=.01)
        self.app.dispose.assert_not_called()

    def test_shutdown_exception_is_logged_with_traceback(self):
        self.app._recorder.stop.side_effect = OSError("écriture impossible")
        with self.assertLogs("stepwatch.ui", level="ERROR") as logs:
            self.assertFalse(self.app.shutdown(timeout=.01))
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertIn("écriture impossible", logs.output[0])

    def test_shutdown_skips_background_threads_whose_start_failed(self):
        self.app._recorder = None
        self.app._import_thread = threading.Thread(target=lambda: None)
        self.app._export_thread = threading.Thread(target=lambda: None)
        self.assertTrue(self.app.shutdown(timeout=.01))
        self.assertIsNone(self.app._import_thread.ident)
        self.assertIsNone(self.app._export_thread.ident)


if __name__ == "__main__":
    unittest.main()
