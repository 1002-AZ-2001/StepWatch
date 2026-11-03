"""Opt-in end-to-end MSS capture on a Linux virtual display, without Win32 hooks."""
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest

from PIL import Image

from stepwatch.engine import Recorder
from stepwatch.models import CaptureConfig, CaptureTarget, Rect
from stepwatch.storage import SessionStore


@unittest.skipUnless(os.environ.get("STEPWATCH_GUI_TESTS") == "1" and os.environ.get("DISPLAY"),
                     "Capture réelle Linux : affichage virtuel et STEPWATCH_GUI_TESTS=1 requis.")
class LiveCaptureTests(unittest.TestCase):
    def test_visible_pixels_flow_through_mss_redaction_and_offline_export(self):
        root = tk.Tk()
        recorder = None
        try:
            root.overrideredirect(True)
            root.geometry("400x300+100+100")
            root.configure(bg="#ffffff")
            root.update()
            with tempfile.TemporaryDirectory() as temporary:
                store = SessionStore(Path(temporary), "Test réel Linux MSS")
                recorder = Recorder(CaptureTarget("region", "Fenêtre de test", Rect(100, 100, 400, 300)),
                                    CaptureConfig(mode="visual", interval=.1,
                                                  masks=(Rect(10, 10, 40, 40),)), store)
                recorder.start()

                def wait_for_count(count):
                    deadline = time.monotonic() + 5
                    while len(store.steps) < count and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertGreaterEqual(len(store.steps), count, recorder.state)

                wait_for_count(1)
                root.configure(bg="#123456")
                root.update()
                wait_for_count(2)
                self.assertTrue(recorder.stop())
                self.assertEqual(recorder.state, "stopped")
                metadata = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
                self.assertEqual([s["trigger"] for s in metadata["steps"]], ["initial", "visual_change"])
                for step, color in zip(metadata["steps"], ((255, 255, 255), (18, 52, 86))):
                    with Image.open(store.directory / step["image"]) as image:
                        self.assertEqual(image.size, (400, 300))
                        self.assertEqual(image.getpixel((200, 200)), color)
                        self.assertEqual(image.getpixel((20, 20)), (0, 0, 0))
                report = (store.directory / "rapport.html").read_text(encoding="utf-8")
                self.assertIn("Changement visuel détecté", report)
                self.assertIn('src="captures/000002.png"', report)
        finally:
            if recorder is not None:
                recorder.stop()
            root.destroy()
