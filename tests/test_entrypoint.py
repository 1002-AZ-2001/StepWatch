"""Diagnostic descriptors and exception hooks are released after the UI stops."""

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
                     patch("stepwatch.ui.run_app", side_effect=run):
                    if error:
                        with self.assertRaisesRegex(RuntimeError, "boucle graphique"):
                            main()
                    else:
                        main()
                self.assertEqual(actions, ["dpi", "ui", "reset"])


if __name__ == "__main__":
    unittest.main()
