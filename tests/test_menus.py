"""Menu bar, Paramètres window and the preferences it controls."""

import os
import queue
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from stepwatch.models import CaptureTarget, Rect
from stepwatch.settings import Preferences
from stepwatch.storage import STANDALONE_REPORT, SessionStore, read_session


class FakeRecorder:
    state = "recording"

    def __init__(self):
        self.events = queue.Queue()
        self.store = Mock(directory=None)  # Like Recorder.store: a session store.

    def set_excluded_regions(self, regions, masked=()):
        pass

    def pause(self):
        self.state = "paused"

    def resume(self):
        self.state = "recording"

    def stop(self, timeout=5):
        self.state = "stopped"
        return True


@unittest.skipUnless(
    os.environ.get("STEPWATCH_GUI_TESTS") == "1"
    and (sys.platform == "win32" or os.environ.get("DISPLAY")),
    "Interface : activer STEPWATCH_GUI_TESTS=1 avec un bureau Windows ou DISPLAY.",
)
class MenuAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prefs = Preferences(Path(self.temp.name) / "conf" / "settings.json")
        self.root = tk.Tk()
        backend = Mock()
        backend.list_windows.return_value = [CaptureTarget("window", "Client", hwnd=1)]
        backend.list_monitors.return_value = []
        backend.resolve.return_value = Rect(0, 0, 800, 600)
        from stepwatch.ui import StepWatchApp
        with patch("stepwatch.ui.capture_backend_factory", return_value=Mock(return_value=backend)):
            self.app = StepWatchApp(self.root, preferences=self.prefs)
        self.app.output.set(self.temp.name)
        store = SessionStore(Path(self.temp.name), "Guide menus")
        for colour in ("red", "green"):
            store.save(Image.new("RGB", (32, 24), colour), "local_click", click=(2, 2))
        store.close()
        self.directory = store.directory
        self.root.update()

    def tearDown(self):
        app, root = self.app, self.root
        try:
            app._recorder = None
            app.shutdown(timeout=5)
            app.dispose()
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass
            self.app = self.root = None
            self.temp.cleanup()

    def pump(self, condition=lambda: True, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app._poll()
            self.root.update()
            if condition():
                return
            time.sleep(.01)
        self.fail("Condition non atteinte")

    def entry_state(self, menu, label):
        return str(self.app.menus[menu].entrycget(label, "state"))

    def test_menu_bar_lists_every_area(self):
        bar = self.app.menubar
        labels = [bar.entrycget(index, "label") for index in range(bar.index("end") + 1)]
        self.assertEqual(labels, ["Fichier", "Enregistrement", "Guide", "Affichage", "Aide"])
        self.assertEqual(self.app.menus["Fichier"].entrycget("Paramètres…", "accelerator"), "Ctrl+,")

    def test_entry_states_follow_the_recording(self):
        self.app._refresh_menus()
        self.assertEqual(self.entry_state("Enregistrement", "Démarrer l’enregistrement"), "normal")
        self.assertEqual(self.entry_state("Enregistrement", "Arrêter"), "disabled")
        self.assertEqual(self.entry_state("Guide", "Supprimer l’étape"), "disabled")
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump()
        self.app._refresh_menus()
        self.assertEqual(self.entry_state("Enregistrement", "Démarrer l’enregistrement"), "disabled")
        self.assertEqual(self.entry_state("Enregistrement", "Arrêter"), "normal")
        self.app.menus["Enregistrement"].invoke("Pause / Reprendre")
        self.assertEqual(recorder.state, "paused")
        self.app.menus["Enregistrement"].invoke("Arrêter")
        self.assertEqual(recorder.state, "stopped")

    def test_view_menu_and_shortcuts_switch_tabs(self):
        self.app.menus["Affichage"].invoke("Onglet Journal")
        self.assertEqual(self.app._tabs.index("current"), 2)
        self.app._menu_shortcut(lambda: self.app._tabs.select(0))
        self.assertEqual(self.app._tabs.index("current"), 0)
        self.app.menus["Affichage"].invoke("Réglages avancés")
        self.assertTrue(self.app._advanced_open.get())
        self.assertTrue(self.app._advanced.winfo_manager())

    def test_settings_window_saves_and_applies_preferences(self):
        self.app._open_settings()
        dialog = self.app._settings_dialog
        dialog.vars["name_template"].set("Poste {date}")
        dialog.vars["bar_position"].set("En haut de l’écran")
        dialog.vars["keep_retired"].set(False)
        dialog.vars["sound"].set(True)
        dialog.vars["font_scale"].set("115 %")
        dialog._apply()
        values = Preferences(self.prefs.path).values  # Read back from disk.
        self.assertEqual(values["name_template"], "Poste {date}")
        self.assertEqual(values["bar_position"], "haut")
        self.assertFalse(values["keep_retired"])
        self.assertTrue(values["sound"])
        self.assertEqual(values["font_scale"], 115)
        self.assertRegex(self.app.name.get(), r"^Poste \d{2}/\d{2}/\d{4}$")
        self.assertIn("prochain lancement", self.app.log.get("1.0", "end"))
        self.app._reset_settings()
        self.assertEqual(self.prefs.values["bar_position"], "bas")
        self.assertEqual(self.app.name.get(), "Nouvelle intervention")

    def test_deleting_without_keeping_the_capture_cannot_be_undone(self):
        self.prefs.values.update(keep_retired=False, confirm_delete=False)
        review = self.app._review
        review.load(self.directory)
        self.pump(lambda: review.directory == self.directory and not review.busy)
        review.list.selection_set("0")
        self.pump(lambda: review._selected == "0" and not review.busy)
        image = review.steps[0]["image"]
        with patch("stepwatch.review.messagebox.askyesno", return_value=True) as asked:
            review.delete_button.invoke()
        asked.assert_called_once()  # Always confirmed when the capture is erased.
        self.assertFalse((self.directory / image).exists())
        self.assertFalse((self.directory / "captures" / "retirees").exists())
        self.assertEqual(review._undo, [])
        self.prefs.values.update(keep_retired=True, confirm_delete=False)
        review.list.selection_set("0")
        self.pump(lambda: review._selected == "0" and not review.busy)
        with patch("stepwatch.review.messagebox.askyesno") as asked:
            review.delete_button.invoke()
        asked.assert_not_called()
        self.assertEqual(len(read_session(self.directory)[2]), 0)

    def test_after_stop_guide_tab_new_name_and_automatic_standalone_report(self):
        self.prefs.values.update(open_guide_after_stop=True, auto_standalone=True, name_template="Suite {heure}")
        self.app._use_session(self.directory)
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump()
        recorder.stop()
        self.pump(lambda: (self.directory / STANDALONE_REPORT).exists()
                  and not self.app._background_running(), timeout=10)
        self.assertEqual(self.app._tabs.select(), str(self.app._review))
        self.assertRegex(self.app.name.get(), r"^Suite \d{2}:\d{2}$")

    def test_floating_bar_at_the_top_when_requested(self):
        self.prefs.values["bar_position"] = "haut"
        self.app._recorder = FakeRecorder()
        self.app._compact_session = True
        self.pump(lambda: self.app._bar is not None and self.app._bar.winfo_ismapped())
        self.assertLess(self.app._bar.winfo_rooty(), 100)

    def test_check_installation_reports_success(self):
        with patch("stepwatch.ui.messagebox.showinfo") as shown:
            self.app.menus["Aide"].invoke("Vérifier l’installation")
            self.pump(lambda: shown.called, timeout=15)
        self.assertIn("Installation vérifiée", shown.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
