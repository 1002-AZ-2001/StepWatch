"""Éditeur de guide et barre flottante d'enregistrement, avec la vraie interface Qt."""

import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import qt_helpers  # noqa: F401 - plateforme Qt hors écran, avant toute fenêtre
from qt_helpers import close_app, log_text, make_app, pump

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stepwatch.guide import RETIRED_FOLDER
from stepwatch.storage import STANDALONE_REPORT, SessionStore, export_standalone, read_session
from stepwatch.ui import LOCAL_HOTKEYS


class FakeRecorder:
    state = "recording"

    def __init__(self):
        self.events = queue.Queue()
        self.regions = None
        self.notes = []

    def set_excluded_regions(self, regions, masked=()):
        self.regions = (list(regions), list(masked))

    def pause(self):
        self.state = "paused"

    def resume(self):
        self.state = "recording"

    def stop(self, timeout=5):
        self.state = "stopped"
        return True


class GuideInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = make_app()
        self.app.output.set(self.temp.name)
        store = SessionStore(Path(self.temp.name), "Guide de test")
        for colour in ("red", "green", "blue"):
            store.save(Image.new("RGB", (64, 48), colour), "local_click", click=(5, 6),
                       annotation=f"Clic {colour}.")
        store.close()
        self.directory = store.directory

    def tearDown(self):
        app = self.app
        try:
            app._recorder = None
            # Attendre la lecture d'image, comme une fermeture normale, avant de libérer Qt.
            self.assertTrue(app.shutdown(timeout=5))
        finally:
            close_app(app)
            self.app = None
            self.temp.cleanup()

    def pump(self, condition=lambda: True, timeout=5):
        pump(condition, timeout, app=self.app)

    def open_guide(self):
        review = self.app._review
        review.load(self.directory)
        self.pump(lambda: review.directory == self.directory and not review.busy)
        return review

    def select(self, review, position):
        review.select(position)
        self.pump(lambda: review._selected == str(position) and not review.busy)

    def test_titles_descriptions_and_guide_name_are_saved(self):
        review = self.open_guide()
        self.assertEqual(review.guide_name.get(), "Guide de test")
        self.assertEqual(review.name_entry.text(), "Guide de test")
        self.select(review, 1)
        review.title_entry.setText("Ouvrir la configuration")
        review.annotation.setPlainText("Cliquer sur Paramètres.")
        review._commit_step()
        review.name_entry.setText("Guide DNS")
        review._commit_name()
        name, _, steps = read_session(self.directory)
        self.assertEqual(name, "Guide DNS")
        self.assertEqual(steps[1]["title"], "Ouvrir la configuration")
        self.assertEqual(steps[1]["annotation"], "Cliquer sur Paramètres.")
        self.assertEqual(review.item(1).text(1), "Ouvrir la configuration")
        self.assertIn("Ouvrir la configuration", (self.directory / "rapport.html").read_text(encoding="utf-8"))

    def test_editing_finished_and_focus_out_save_the_texts(self):
        review = self.open_guide()
        self.app._tabs.setCurrentWidget(review)
        self.select(review, 0)
        review.title_entry.setText("Titre saisi")
        review.title_entry.editingFinished.emit()
        self.assertEqual(read_session(self.directory)[2][0]["title"], "Titre saisi")
        review.annotation.setFocus()
        review.annotation.setPlainText("Description saisie")
        review._annotation_filter.eventFilter(review.annotation, _FocusOut())
        self.assertEqual(read_session(self.directory)[2][0]["annotation"], "Description saisie")

    def test_move_delete_and_redact_steps(self):
        review = self.open_guide()
        images = [step["image"] for step in review.steps]
        self.select(review, 0)
        review.down_button.click()
        self.assertEqual([step["image"] for step in review.steps], [images[1], images[0], images[2]])
        self.pump(lambda: review._selected == "1" and not review.busy)  # L'étape déplacée reste sélectionnée.

        export_standalone(self.directory)
        with patch("stepwatch.review.messagebox.askyesno", return_value=True):
            review.delete_button.click()
        self.assertEqual(len(review.steps), 2)
        self.assertTrue((self.directory / "captures" / RETIRED_FOLDER / Path(images[0]).name).is_file())
        self.assertFalse((self.directory / STANDALONE_REPORT).exists())
        self.assertIn("rapport autonome", log_text(self.app).lower())

        self.select(review, 0)

        def draw(dialog):
            dialog.show()
            QTest.qWaitForWindowExposed(dialog)
            canvas = dialog.canvas
            # QPoint(0, 0) vaut « centre » pour QTest : partir de (1, 1).
            QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
            QTest.mouseMove(canvas, canvas.rect().center())
            QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center())
            self.assertTrue(dialog.apply_button.isEnabled())
            dialog.apply_button.click()

        review.dialog_runner = draw
        review.redact_button.click()
        self.pump(lambda: not review.busy)
        with Image.open(self.directory / review.steps[0]["image"]) as image:
            self.assertEqual(image.getpixel((5, 5)), (0, 0, 0))
            self.assertNotEqual(image.getpixel((60, 45)), (0, 0, 0))

    def test_redaction_dialog_can_be_cancelled(self):
        review = self.open_guide()
        self.select(review, 0)
        review.dialog_runner = lambda dialog: dialog.reject()
        review.redact_button.click()
        with Image.open(self.directory / review.steps[0]["image"]) as image:
            self.assertEqual(image.getpixel((5, 5)), (255, 0, 0))

    def test_undo_reverts_order_title_name_and_removal(self):
        review = self.open_guide()
        images = [step["image"] for step in review.steps]
        self.assertFalse(review.undo_button.isEnabled())
        self.select(review, 0)
        review.down_button.click()
        self.pump(lambda: review._selected == "1" and not review.busy)
        review.step_title_text.set("Titre provisoire")
        review._commit_step()
        review.guide_name.set("Autre nom")
        review._commit_name()
        with patch("stepwatch.review.messagebox.askyesno", return_value=True):
            review.delete_button.click()
        self.assertEqual(len(review.steps), 2)

        review.undo()  # Suppression : la capture revient de captures/retirees/.
        self.assertEqual(len(review.steps), 3)
        self.assertFalse(any((self.directory / "captures" / RETIRED_FOLDER).glob("*.png")))
        review.undo()  # Nom du guide.
        self.assertEqual(read_session(self.directory)[0], "Guide de test")
        review.undo()  # Titre de l'étape.
        self.assertEqual([step["title"] for step in read_session(self.directory)[2]], ["", "", ""])
        review.undo()  # Ordre.
        self.assertEqual([step["image"] for step in read_session(self.directory)[2]], images)
        self.assertFalse(review.undo_button.isEnabled())

    def key(self, widget, key, modifier=Qt.KeyboardModifier.NoModifier):
        widget.setFocus()
        QApplication.processEvents()
        QTest.keyClick(widget, key, modifier)
        QApplication.processEvents()

    def test_keyboard_shortcuts_act_on_the_selected_step(self):
        review = self.open_guide()
        images = [step["image"] for step in review.steps]
        self.app._tabs.setCurrentWidget(review)  # Raccourcis actifs seulement quand l'onglet Guide est affiché.
        self.app.root.activateWindow()
        self.select(review, 0)
        self.key(review.list, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
        self.pump(lambda: review._selected == "1" and not review.busy)
        self.assertEqual(read_session(self.directory)[2][1]["image"], images[0])
        self.key(review.list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.pump(lambda: read_session(self.directory)[2][0]["image"] == images[0]
                  and review._selected is not None and not review.busy)
        with patch("stepwatch.review.messagebox.askyesno", return_value=True) as asked:
            self.key(review.list, Qt.Key.Key_Delete)
        asked.assert_called_once()
        self.assertEqual(len(read_session(self.directory)[2]), 2)
        # En saisie, Suppr et Ctrl+Z gardent leur sens dans le champ.
        self.select(review, 0)
        with patch("stepwatch.review.messagebox.askyesno") as asked:
            self.key(review.title_entry, Qt.Key.Key_Delete)
        asked.assert_not_called()
        # F2 place le curseur dans le titre de l'étape.
        self.key(review.list, Qt.Key.Key_F2)
        self.assertIs(QApplication.focusWidget(), review.title_entry)
        # Onglet Guide masqué : le raccourci ne fait rien.
        self.app._tabs.setCurrentIndex(0)
        with patch("stepwatch.review.messagebox.askyesno") as asked:
            self.assertFalse(review._shortcut("Delete", review._delete))
        asked.assert_not_called()

    def test_editing_is_disabled_while_recording(self):
        review = self.open_guide()
        self.select(review, 0)
        self.assertTrue(review.title_entry.isEnabled())
        self.app._recorder = FakeRecorder()
        self.app._compact_session = False
        self.app._controls_key = None
        self.pump()
        self.assertFalse(review.title_entry.isEnabled())
        self.assertFalse(review.delete_button.isEnabled())

    def test_floating_bar_replaces_the_window_and_excludes_itself(self):
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = True
        self.app.kept.set("4")
        self.pump(lambda: self.app._bar is not None and recorder.regions is not None)
        bar = self.app._bar
        self.assertFalse(self.app.root.isVisible())
        self.assertIn("4 étapes", bar._count.text())
        regions, masked = recorder.regions
        self.assertEqual(len(regions), 1)  # Seulement la barre : la fenêtre principale est masquée.
        frame = bar.frameGeometry()
        self.assertTrue(regions[0].contains(frame.x() + 2, frame.y() + 2))
        if sys.platform != "win32":
            self.assertEqual(masked, regions)  # Pas d'exclusion des captures hors Windows.
        else:
            # Exclue des captures (Windows 10 2004+), donc pas masquée.
            self.assertEqual(masked, [] if self.app._bar_excluded else regions)
        bar.pause_button.click()
        self.assertEqual(recorder.state, "paused")
        self.pump(lambda: bar.pause_button.text() == "Reprendre")
        bar.expand_button.click()
        self.pump(lambda: len(recorder.regions[0]) == 2)  # Fenêtre principale de nouveau visible.
        bar.stop_button.click()
        self.pump(lambda: self.app._bar is None and self.app.root.isVisible())

    def test_floating_bar_position_follows_the_setting(self):
        self.app._prefs.values["bar_position"] = "haut"
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = True
        self.pump(lambda: self.app._bar is not None)
        area = self.app._bar.screen().availableGeometry()
        self.assertLess(self.app._bar.y() - area.y(), area.height() // 2)

    def test_recording_shortcuts_in_stepwatch_windows(self):
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump()
        self.assertEqual({shortcut.key().toString() for shortcut in self.app._local_hotkeys},
                         set(LOCAL_HOTKEYS.values()))

        def key(name):
            self.key(self.app.root, {"F9": Qt.Key.Key_F9, "F11": Qt.Key.Key_F11}[name],
                     Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)

        key("F9")
        self.assertEqual(recorder.state, "paused")
        key("F9")
        self.assertEqual(recorder.state, "recording")
        key("F11")
        self.assertEqual(recorder.state, "stopped")
        key("F9")  # Inactif une fois arrêté.
        self.assertEqual(recorder.state, "stopped")

    def test_recent_guides_menu_opens_a_guide(self):
        self.app._fill_recent_menu()
        menu = self.app._recent_menu
        labels = [action.text() for action in menu.actions()]
        self.assertIn("Parcourir…", labels)
        entry = next(action for action in menu.actions() if "Guide de test" in action.text())
        entry.trigger()
        self.pump(lambda: self.app._review.directory == self.directory and not self.app._review.busy)
        self.assertIs(self.app._tabs.currentWidget(), self.app._review)

    def test_no_floating_bar_when_compact_mode_is_off(self):
        self.app.compact.set(False)
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump(lambda: recorder.regions is not None)
        self.assertIsNone(self.app._bar)
        self.assertTrue(self.app.root.isVisible())
        self.assertEqual(len(recorder.regions[0]), 1)  # La fenêtre principale elle-même.


def _FocusOut():
    from PySide6.QtGui import QFocusEvent

    return QFocusEvent(QFocusEvent.Type.FocusOut)


if __name__ == "__main__":
    unittest.main()
