"""Diagnostic descriptors and exception hooks are released after the UI stops."""

import io
from pathlib import Path
import unittest
from unittest.mock import patch

from stepwatch.__main__ import main


class EntrypointTests(unittest.TestCase):
    def test_main_releases_diagnostics_after_normal_exit_and_failure(self):
        for error in (None, RuntimeError("boucle graphique interrompue")):
            with self.subTest(error=error):
                actions = []

                def run(log_path):
                    self.assertEqual(log_path, Path("test.log"))
                    actions.append("ui")
                    if error:
                        raise error

                with patch("stepwatch.__main__.diagnostics.setup", return_value=Path("test.log")), \
                     patch("stepwatch.__main__.diagnostics.reset", side_effect=lambda: actions.append("reset")), \
                     patch("stepwatch.__main__.enable_dpi_awareness", side_effect=lambda: actions.append("dpi")), \
                     patch("stepwatch.__main__.display_problem", return_value=None), \
                     patch("stepwatch.ui.run_app", side_effect=run):
                    if error:
                        with self.assertRaisesRegex(RuntimeError, "boucle graphique"):
                            main([])
                    else:
                        self.assertEqual(main([]), 0)
                self.assertEqual(actions, ["dpi", "ui", "reset"])

    def test_missing_display_is_explained_without_starting_tk(self):
        with patch("stepwatch.__main__.diagnostics.setup", return_value=None), \
             patch("stepwatch.__main__.diagnostics.reset"), \
             patch("stepwatch.__main__.display_problem", return_value="Session Wayland sans XWayland"), \
             patch("stepwatch.ui.run_app") as run_app, \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(main([]), 2)
        run_app.assert_not_called()
        self.assertIn("Session Wayland sans XWayland", stderr.getvalue())

    def test_self_test_option_runs_the_self_test_only(self):
        with patch("stepwatch.__main__.diagnostics.setup", return_value=None), \
             patch("stepwatch.__main__.diagnostics.reset"), \
             patch("stepwatch.selftest.run", return_value=0) as run, \
             patch("stepwatch.ui.run_app") as run_app:
            self.assertEqual(main(["--self-test"]), 0)
        run.assert_called_once_with()
        run_app.assert_not_called()

    def test_unknown_arguments_from_launchers_are_ignored(self):
        with patch("stepwatch.__main__.diagnostics.setup", return_value=None), \
             patch("stepwatch.__main__.diagnostics.reset"), \
             patch("stepwatch.__main__.display_problem", return_value=None), \
             patch("stepwatch.ui.run_app") as run_app:
            self.assertEqual(main(["--fichier-inconnu"]), 0)
        run_app.assert_called_once()


if __name__ == "__main__":
    unittest.main()
