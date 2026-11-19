"""Guide editor and floating recording bar, with a real Tk interface."""

import gc
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

from stepwatch.guide import RETIRED_FOLDER
from stepwatch.models import CaptureTarget, Rect
from stepwatch.storage import STANDALONE_REPORT, SessionStore, export_standalone, read_session
from stepwatch.ui import StepWatchApp


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


@unittest.skipUnless(
    os.environ.get("STEPWATCH_GUI_TESTS") == "1"
    and (sys.platform == "win32" or os.environ.get("DISPLAY")),
    "Interface : activer STEPWATCH_GUI_TESTS=1 avec un bureau Windows ou DISPLAY.",
)
class GuideInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        backend = Mock()
        backend.list_windows.return_value = [CaptureTarget("window", "Client", hwnd=123)]
        backend.list_monitors.return_value = []
        backend.resolve.return_value = Rect(0, 0, 1280, 720)
        with patch("stepwatch.ui.capture_backend_factory", return_value=Mock(return_value=backend)):
            self.app = StepWatchApp(self.root)
        self.app.output.set(self.temp.name)
        store = SessionStore(Path(self.temp.name), "Guide de test")
        for colour in ("red", "green", "blue"):
            store.save(Image.new("RGB", (64, 48), colour), "local_click", click=(5, 6),
                       annotation=f"Clic {colour}.")
        store.close()
        self.directory = store.directory
        self.root.update()

    def tearDown(self):
        app, root = self.app, self.root
        try:
            app._recorder = None
            # Wait for the image reader, like a normal close, before freeing Tk.
            self.assertTrue(app.shutdown(timeout=5))
            app.dispose()
        finally:
            try:
                root.destroy()  # Never leave a live default root to the next test.
            except tk.TclError:
                pass
            self.app = self.root = None
            del app, root
            gc.collect()
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

    def open_guide(self):
        review = self.app._review
        review.load(self.directory)
        self.pump(lambda: review.directory == self.directory and not review.busy)
        return review

    def select(self, review, position):
        review.list.selection_set(str(position))
        self.pump(lambda: review._selected == str(position) and not review.busy)

    def test_titles_descriptions_and_guide_name_are_saved(self):
        review = self.open_guide()
        self.assertEqual(review.guide_name.get(), "Guide de test")
        self.select(review, 1)
        review.step_title_text.set("Ouvrir la configuration")
        review.annotation.delete("1.0", "end")
        review.annotation.insert("1.0", "Cliquer sur Paramètres.")
        review._commit_step()
        review.guide_name.set("Guide DNS")
        review._commit_name()
        name, _, steps = read_session(self.directory)
        self.assertEqual(name, "Guide DNS")
        self.assertEqual(steps[1]["title"], "Ouvrir la configuration")
        self.assertEqual(steps[1]["annotation"], "Cliquer sur Paramètres.")
        self.assertEqual(review.list.item("1", "values")[0], "Ouvrir la configuration")
        self.assertIn("Ouvrir la configuration", (self.directory / "rapport.html").read_text(encoding="utf-8"))

    def test_move_delete_and_redact_steps(self):
        review = self.open_guide()
        images = [step["image"] for step in review.steps]
        self.select(review, 0)
        review.down_button.invoke()
        self.assertEqual([step["image"] for step in review.steps], [images[1], images[0], images[2]])
        self.pump(lambda: review._selected == "1" and not review.busy)  # The moved step stays selected.

        export_standalone(self.directory)
        with patch("stepwatch.review.messagebox.askyesno", return_value=True):
            review.delete_button.invoke()
        self.assertEqual(len(review.steps), 2)
        self.assertTrue((self.directory / "captures" / RETIRED_FOLDER / Path(images[0]).name).is_file())
        self.assertFalse((self.directory / STANDALONE_REPORT).exists())
        self.assertIn("rapport autonome", self.app.log.get("1.0", "end").lower())

        class Dialog:
            def __init__(self, *args, **kwargs):
                self.result = [(0, 0, 10, 10)]

            def grab_set(self):
                pass

        self.select(review, 0)
        with patch("stepwatch.review.RedactionDialog", Dialog), patch.object(review, "wait_window"):
            review.redact_button.invoke()
        with Image.open(self.directory / review.steps[0]["image"]) as image:
            self.assertEqual(image.getpixel((5, 5)), (0, 0, 0))

    def test_editing_is_disabled_while_recording(self):
        review = self.open_guide()
        self.select(review, 0)
        self.assertEqual(str(review.title_entry["state"]), "normal")
        self.app._recorder = FakeRecorder()
        self.app._compact_session = False
        self.app._controls_key = None
        self.pump()
        self.assertEqual(str(review.title_entry["state"]), "disabled")
        self.assertEqual(str(review.delete_button["state"]), "disabled")

    def test_floating_bar_replaces_the_window_and_excludes_itself(self):
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = True
        self.app.kept.set("4")
        self.pump(lambda: self.app._bar is not None and recorder.regions is not None)
        bar = self.app._bar
        self.assertFalse(self.root.winfo_ismapped())
        self.assertIn("4 étapes", bar._count.cget("text"))
        regions, masked = recorder.regions
        self.assertEqual(len(regions), 1)  # Only the bar: the main window is withdrawn.
        self.assertTrue(regions[0].contains(bar.winfo_rootx() + 2, bar.winfo_rooty() + 2))
        if sys.platform != "win32":
            self.assertEqual(masked, regions)  # No capture exclusion outside Windows.
        else:
            # Excluded from captures (Windows 10 2004+), hence not masked.
            self.assertEqual(masked, [] if self.app._bar_excluded else regions)
        bar.pause_button.invoke()
        self.assertEqual(recorder.state, "paused")
        self.pump(lambda: bar.pause_button.cget("text") == "Reprendre")
        bar.expand_button.invoke()
        self.pump(lambda: len(recorder.regions[0]) == 2)  # Main window visible again.
        bar.stop_button.invoke()
        self.pump(lambda: self.app._bar is None and self.root.winfo_ismapped())

    def test_no_floating_bar_when_compact_mode_is_off(self):
        self.app.compact.set(False)
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump(lambda: recorder.regions is not None)
        self.assertIsNone(self.app._bar)
        self.assertTrue(self.root.winfo_ismapped())
        self.assertEqual(len(recorder.regions[0]), 1)  # The main window itself.


if __name__ == "__main__":
    unittest.main()
