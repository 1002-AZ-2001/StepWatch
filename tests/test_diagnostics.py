"""Diagnostic log: file, rotation settings, crash hooks and their removal."""

import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from stepwatch import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(diagnostics.reset)
        self.directory = Path(self.temporary.name) / "logs"

    def read_log(self):
        for handler in logging.getLogger().handlers:
            handler.flush()
        return (self.directory / "stepwatch.log").read_text(encoding="utf-8")

    def test_messages_go_to_a_bounded_rotating_file(self):
        path = diagnostics.setup(self.directory)
        self.assertEqual(path, self.directory / "stepwatch.log")
        logging.getLogger("stepwatch.essai").info("Intervention démarrée")
        logging.getLogger("stepwatch.essai").debug("Comparaison ignorée")
        logging.getLogger("bibliotheque").info("Détail d’une bibliothèque")
        text = self.read_log()
        self.assertIn("StepWatch", text.splitlines()[0])
        self.assertIn("Intervention démarrée", text)
        self.assertNotIn("Comparaison ignorée", text)
        self.assertNotIn("Détail d’une bibliothèque", text)
        handler = next(h for h in logging.getLogger().handlers
                       if getattr(h, "baseFilename", "") == str(path))
        self.assertEqual((handler.maxBytes, handler.backupCount), (1_000_000, 3))

    def test_uncaught_exceptions_are_logged_with_their_traceback(self):
        with patch.object(threading, "excepthook", lambda arguments: None), \
                patch.object(sys, "excepthook", lambda *arguments: None):
            diagnostics.setup(self.directory)

            def failing():
                raise RuntimeError("panne du thread de capture")

            worker = threading.Thread(target=failing, name="StepWatch-essai")
            worker.start()
            worker.join()
            try:
                raise ValueError("panne du thread principal")
            except ValueError:
                sys.excepthook(*sys.exc_info())
            diagnostics.reset()
        text = self.read_log() if (self.directory / "stepwatch.log").exists() else ""
        self.assertIn("Exception non gérée dans le thread StepWatch-essai", text)
        self.assertIn("RuntimeError: panne du thread de capture", text)
        self.assertIn("ValueError: panne du thread principal", text)
        self.assertIn("Traceback", text)

    def test_setup_twice_does_not_duplicate_lines_and_reset_restores_hooks(self):
        original_hooks = (sys.excepthook, threading.excepthook)
        diagnostics.setup(self.directory)
        diagnostics.setup(self.directory)
        logging.getLogger("stepwatch").info("Une seule fois")
        self.assertEqual(self.read_log().count("Une seule fois"), 1)
        diagnostics.reset()
        self.assertEqual((sys.excepthook, threading.excepthook), original_hooks)
        self.assertFalse(any(getattr(h, "baseFilename", "").startswith(str(self.directory))
                             for h in logging.getLogger().handlers))

    def test_in_flight_hooks_keep_their_original_chain_after_reset(self):
        previous_sys, previous_thread = Mock(), Mock()
        error = ValueError("exception déjà en cours")
        arguments = threading.ExceptHookArgs((ValueError, error, None, None))
        with patch.object(sys, "excepthook", previous_sys), \
                patch.object(threading, "excepthook", previous_thread):
            diagnostics.setup(self.directory)
            retained_sys, retained_thread = sys.excepthook, threading.excepthook
            diagnostics.reset()
            retained_sys(ValueError, error, None)
            retained_thread(arguments)
        previous_sys.assert_called_once_with(ValueError, error, None)
        previous_thread.assert_called_once_with(arguments)

    def test_reset_preserves_hooks_installed_after_our_setup(self):
        later_sys, later_thread = Mock(), Mock()
        with patch.object(sys, "excepthook", Mock()), \
                patch.object(threading, "excepthook", Mock()):
            diagnostics.setup(self.directory)
            sys.excepthook = later_sys
            threading.excepthook = later_thread
            diagnostics.reset()
            self.assertIs(sys.excepthook, later_sys)
            self.assertIs(threading.excepthook, later_thread)

    def test_reset_closes_our_log_and_native_crash_descriptors(self):
        with patch.object(diagnostics.faulthandler, "is_enabled", return_value=False), \
                patch.object(diagnostics.faulthandler, "enable") as enable, \
                patch.object(diagnostics.faulthandler, "disable") as disable:
            diagnostics.setup(self.directory)
            crash_file = enable.call_args.kwargs["file"]
            handler = next(h for h in logging.getLogger().handlers
                           if getattr(h, "baseFilename", "") == str(self.directory / "stepwatch.log"))
            self.assertFalse(crash_file.closed)
            diagnostics.reset()
            diagnostics.reset()
            self.assertTrue(crash_file.closed)
            self.assertIsNone(handler.stream)
            disable.assert_called_once()

    def test_failed_native_crash_setup_closes_its_file(self):
        files = []

        def fail_enable(**arguments):
            files.append(arguments["file"])
            raise OSError("gestionnaire natif indisponible")

        with patch.object(diagnostics.faulthandler, "is_enabled", return_value=False), \
                patch.object(diagnostics.faulthandler, "enable", side_effect=fail_enable), \
                patch.object(diagnostics.faulthandler, "disable") as disable:
            self.assertIsNotNone(diagnostics.setup(self.directory))
            self.assertTrue(files[0].closed)
            diagnostics.reset()
            disable.assert_not_called()

    def test_existing_native_crash_handler_is_left_enabled(self):
        with patch.object(diagnostics.faulthandler, "is_enabled", return_value=True), \
                patch.object(diagnostics.faulthandler, "enable") as enable, \
                patch.object(diagnostics.faulthandler, "disable") as disable:
            diagnostics.setup(self.directory)
            diagnostics.reset()
            enable.assert_not_called()
            disable.assert_not_called()

    def test_unwritable_location_never_prevents_starting(self):
        blocker = Path(self.temporary.name) / "fichier"
        blocker.write_text("x", encoding="utf-8")
        self.assertIsNone(diagnostics.setup(blocker / "logs"))

    def test_default_location_is_local_to_the_user(self):
        with patch.object(diagnostics.sys, "platform", "win32"), \
                patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\tech\AppData\Local"}):
            self.assertEqual(diagnostics.log_directory(),
                             Path(r"C:\Users\tech\AppData\Local") / "StepWatch" / "logs")
        with patch.object(diagnostics.sys, "platform", "linux"), \
                patch.dict(os.environ, {"XDG_STATE_HOME": "/home/tech/.local/state"}):
            self.assertEqual(diagnostics.log_directory(), Path("/home/tech/.local/state/stepwatch"))


if __name__ == "__main__":
    unittest.main()
