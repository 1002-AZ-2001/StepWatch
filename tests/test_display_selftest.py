"""Wayland/X11 session checks and the packaged self-test."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stepwatch import selftest
from stepwatch.display import WINDOW_CLASS, display_problem, session_type

GUI_TESTS = os.environ.get("STEPWATCH_GUI_TESTS") == "1" and bool(os.environ.get("DISPLAY"))


class DisplayTests(unittest.TestCase):
    def test_x11_and_xwayland_sessions_can_start_tk(self):
        self.assertIsNone(display_problem("linux", {"DISPLAY": ":0"}))
        # Wayland session with XWayland: compositors export both variables.
        self.assertIsNone(display_problem("linux", {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}))

    def test_wayland_without_xwayland_names_the_package_to_install(self):
        message = display_problem("linux", {"WAYLAND_DISPLAY": "wayland-0"})
        self.assertIn("XWayland", message)
        self.assertIn("xwayland", message)
        self.assertIn("xorg-xwayland", message)

    def test_no_graphical_session_is_explained(self):
        self.assertIn("Aucun affichage", display_problem("linux", {}))

    def test_windows_and_macos_do_not_need_an_x11_display(self):
        self.assertIsNone(display_problem("win32", {}))
        self.assertIsNone(display_problem("darwin", {}))

    def test_session_type(self):
        self.assertEqual(session_type({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}), "wayland")
        self.assertEqual(session_type({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}), "wayland")
        self.assertEqual(session_type({"DISPLAY": ":0"}), "x11")
        self.assertEqual(session_type({"XDG_SESSION_TYPE": "tty"}), "inconnue")

    def test_desktop_file_matches_the_tk_window_class(self):
        desktop = Path(__file__).resolve().parents[1] / "packaging" / "linux" / "stepwatch.desktop"
        # Tk capitalises the first letter of className to build WM_CLASS.
        self.assertIn(f"StartupWMClass={WINDOW_CLASS.capitalize()}\n", desktop.read_text(encoding="utf-8"))


class SelfTestTests(unittest.TestCase):
    def test_report_checks_run_on_a_temporary_synthetic_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            selftest.check_reports(Path(temporary))
            sessions = sorted(path.name for path in Path(temporary).iterdir())
        self.assertEqual(len(sessions), 2)  # The session and its ShareX import.

    def test_run_reports_success_and_skips_the_interface_without_display(self):
        messages = []
        with patch("stepwatch.selftest.display_problem", return_value="Aucun affichage graphique"):
            self.assertEqual(selftest.run(messages.append), 0)
        self.assertIn("Images, rapports, notes et import ShareX : OK", messages)
        self.assertTrue(any(m.startswith("Interface Tk : non vérifiée") for m in messages))
        self.assertEqual(messages[-1], "Auto-test réussi.")

    def test_run_reports_failures_with_exit_code_1(self):
        messages = []
        with patch("stepwatch.selftest.check_reports", side_effect=RuntimeError("masque non appliqué")):
            self.assertEqual(selftest.run(messages.append), 1)
        self.assertIn("Auto-test en échec : masque non appliqué", messages)

    @unittest.skipUnless(GUI_TESTS, "STEPWATCH_GUI_TESTS=1 et un affichage X11 requis")
    def test_interface_check_opens_and_closes_the_real_window(self):
        messages = []
        self.assertEqual(selftest.run(messages.append), 0)
        self.assertIn("Interface Tk : OK", messages)


if __name__ == "__main__":
    unittest.main()
