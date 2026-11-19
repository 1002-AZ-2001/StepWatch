"""Editing a recorded session as a guide."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from stepwatch.guide import RETIRED_FOLDER, redact_step, remove_step, save_guide
from stepwatch.storage import (MARKDOWN_REPORT, STANDALONE_REPORT, SessionStore, StorageError,
                               export_standalone, read_session, step_heading)


class GuideEditingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        store = SessionStore(Path(self.temporary.name), "Poste client")
        for colour in ("red", "green", "blue"):
            store.save(Image.new("RGB", (40, 30), colour), "local_click", click=(5, 6),
                       annotation=f"Clic sur {colour}.")
        store.close()
        self.directory = store.directory
        self.name, _, self.steps = read_session(self.directory)

    def reload(self):
        self.name, _, self.steps = read_session(self.directory)
        return self.steps

    def test_titles_descriptions_and_name_reach_every_report(self):
        self.steps[1]["title"] = "  Ouvrir   les propriétés  "
        self.steps[1]["annotation"] = "Double-cliquer sur IPv4.\nVérifier le DNS."
        save_guide(self.directory, "Guide DNS <client>", self.steps)
        name, _, steps = read_session(self.directory)
        self.assertEqual(name, "Guide DNS <client>")
        self.assertEqual(steps[1]["title"], "Ouvrir les propriétés")
        self.assertEqual(step_heading(steps[1]), "Ouvrir les propriétés")
        self.assertEqual(step_heading(steps[0]), "Clic local")  # Default: trigger label.
        html = (self.directory / "rapport.html").read_text(encoding="utf-8")
        self.assertIn("Étape 2 — Ouvrir les propriétés", html)
        self.assertIn('<p class="kind">Clic local</p>', html)
        self.assertIn("Guide DNS &lt;client&gt;", html)
        markdown = (self.directory / MARKDOWN_REPORT).read_text(encoding="utf-8")
        self.assertIn("## Étape 2 — Ouvrir les propriétés", markdown)
        self.assertIn("Vérifier le DNS.", markdown)

    def test_reorder_renumbers_and_keeps_images(self):
        images = [step["image"] for step in self.steps]
        save_guide(self.directory, self.name, [self.steps[2], self.steps[0], self.steps[1]])
        steps = self.reload()
        self.assertEqual([step["index"] for step in steps], [1, 2, 3])
        self.assertEqual([step["image"] for step in steps], [images[2], images[0], images[1]])

    def test_foreign_or_duplicated_captures_are_refused(self):
        duplicate = [self.steps[0], dict(self.steps[0])]
        with self.assertRaises(StorageError):
            save_guide(self.directory, self.name, duplicate)
        foreign = dict(self.steps[0], image="captures/999999.png")
        with self.assertRaises(StorageError):
            save_guide(self.directory, self.name, [foreign])
        escape = dict(self.steps[0], image="../secret.png")
        with self.assertRaises(StorageError):
            save_guide(self.directory, self.name, [escape])
        self.assertEqual(len(self.reload()), 3)  # Nothing was written.

    def test_removed_step_capture_moves_to_retired_folder(self):
        removed = self.steps[1]["image"]
        remove_step(self.directory, self.name, self.steps, 1)
        steps = self.reload()
        self.assertEqual(len(steps), 2)
        self.assertNotIn(removed, [step["image"] for step in steps])
        retired = self.directory / "captures" / RETIRED_FOLDER / Path(removed).name
        self.assertTrue(retired.is_file())
        self.assertFalse((self.directory / removed).exists())
        self.assertNotIn(Path(removed).name, (self.directory / "rapport.html").read_text(encoding="utf-8"))

    def test_redaction_replaces_pixels_and_drops_the_stale_standalone_report(self):
        export_standalone(self.directory)
        self.assertTrue((self.directory / STANDALONE_REPORT).exists())
        deleted = redact_step(self.directory, self.name, self.steps, 0, [(30, 25, 10, 5), (0, 0, 0, 0)])  # Any corner order.
        self.assertTrue(deleted)
        self.assertFalse((self.directory / STANDALONE_REPORT).exists())
        with Image.open(self.directory / self.steps[0]["image"]) as image:
            self.assertEqual(image.getpixel((15, 10)), (0, 0, 0))
            self.assertEqual(image.getpixel((2, 2)), (255, 0, 0))
        self.assertEqual(list((self.directory / "captures").glob(".stepwatch-*")), [])

    def test_empty_redaction_is_refused_without_touching_the_capture(self):
        before = (self.directory / self.steps[0]["image"]).read_bytes()
        with self.assertRaises(ValueError):
            redact_step(self.directory, self.name, self.steps, 0, [(60, 60, 90, 90)])  # Outside the image.
        self.assertEqual((self.directory / self.steps[0]["image"]).read_bytes(), before)

    def test_steps_json_keeps_utc_start_and_format(self):
        _, started_at, _ = read_session(self.directory)
        save_guide(self.directory, "", self.steps)
        metadata = json.loads((self.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["format_version"], 1)
        self.assertEqual(metadata["started_at"], started_at)
        self.assertEqual(metadata["name"], "Intervention")


if __name__ == "__main__":
    unittest.main()
