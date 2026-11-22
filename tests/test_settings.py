"""Preferences kept between launches."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stepwatch.settings import RECENT_LIMIT, Preferences, default_path


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "conf" / "settings.json"

    def test_default_locations(self):
        self.assertEqual(default_path("win32", {"APPDATA": r"C:\Users\t\AppData\Roaming"}),
                         Path(r"C:\Users\t\AppData\Roaming") / "StepWatch" / "settings.json")
        self.assertEqual(default_path("linux", {"XDG_CONFIG_HOME": "/home/t/.config"}),
                         Path("/home/t/.config/stepwatch/settings.json"))

    def test_missing_file_gives_defaults_and_save_round_trips(self):
        preferences = Preferences(self.path)
        self.assertTrue(preferences.values["compact"])
        preferences.values.update(output="/srv/guides", compact=False, masks="0,0,10,10")
        self.assertTrue(preferences.save())
        again = Preferences(self.path)
        self.assertEqual(again.values["output"], "/srv/guides")
        self.assertFalse(again.values["compact"])
        self.assertEqual(again.values["masks"], "0,0,10,10")
        self.assertEqual(list(self.path.parent.glob(".settings-*")), [])

    def test_corrupt_or_mistyped_values_fall_back_to_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{pas du json", encoding="utf-8")
        self.assertEqual(Preferences(self.path).values["interval"], "1,0")
        self.path.write_text(json.dumps({"compact": "oui", "output": 3, "geometry": "énorme",
                                         "recent": ["/a", 5, ""], "inconnu": 1}), encoding="utf-8")
        values = Preferences(self.path).values
        self.assertTrue(values["compact"])
        self.assertEqual(values["output"], "")
        self.assertEqual(values["geometry"], "")
        self.assertEqual(values["recent"], ["/a"])
        self.assertNotIn("inconnu", values)

    def test_recent_guides_are_unique_most_recent_first_and_bounded(self):
        preferences = Preferences(self.path)
        for index in range(RECENT_LIMIT + 3):
            preferences.remember_guide(Path(f"/guides/{index}"))
        preferences.remember_guide(Path("/guides/5"))
        recent = preferences.values["recent"]
        self.assertEqual(len(recent), RECENT_LIMIT)
        self.assertEqual(recent[0], str(Path("/guides/5")))
        self.assertEqual(recent.count(str(Path("/guides/5"))), 1)

    def test_unwritable_location_never_raises(self):
        blocker = Path(self.temporary.name) / "fichier"
        blocker.write_text("x")
        preferences = Preferences(blocker / "settings.json")
        self.assertFalse(preferences.save())


class InterfacePreferencesTests(unittest.TestCase):
    def make_app(self, preferences):
        import qt_helpers

        return qt_helpers.make_app(preferences=preferences)

    def close(self, app):
        import qt_helpers

        qt_helpers.close_app(app)

    def test_settings_are_restored_on_next_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            app = self.make_app(Preferences(path))
            try:
                app.output.set(temporary)
                app.source.set("Moniteur")
                app.mode.set("Clics locaux uniquement")
                app.sensitivity.set("3,5")
                app.compact.set(False)
                app.masks_text.setPlainText("1,2,3,4")
                app._toggle_advanced()
                app.root.resize(790, 740)  # Écran hors écran de 800 × 800.
                app._store_preferences()
            finally:
                self.close(app)
            self.assertRegex(Preferences(path).values["geometry"], r"^790x740\+-?\d+\+-?\d+$")
            app = self.make_app(Preferences(path))
            try:
                self.assertEqual(app.output.get(), temporary)
                self.assertEqual(app.source.get(), "Moniteur")
                self.assertEqual(app.source_combo.currentText(), "Moniteur")
                self.assertEqual(app.mode.get(), "Clics locaux uniquement")
                self.assertEqual(app.sensitivity.get(), "3,5")
                self.assertFalse(app.compact.get())
                self.assertEqual(app.masks_text.toPlainText(), "1,2,3,4")
                self.assertTrue(app._advanced_open.get())
                self.assertEqual((app.root.width(), app.root.height()), (790, 740))
            finally:
                self.close(app)

    def test_without_preferences_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary, "APPDATA": temporary}):
            app = self.make_app(None)
            try:
                app._store_preferences()
            finally:
                self.close(app)
            self.assertEqual(list(Path(temporary).rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
