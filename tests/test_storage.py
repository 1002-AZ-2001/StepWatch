import base64
import hashlib
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from stepwatch.storage import (ReportWriteError, SessionStore, StorageError, _local_time,
                               export_standalone, import_sharex)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.image = Image.new("RGB", (32, 24), "#135a8f")

    def tearDown(self):
        self.temporary.cleanup()

    def test_saves_png_json_and_offline_html(self):
        store = SessionStore(self.root, "Intervention écran")
        step = store.save(self.image, "local_click", (12, 7), "Ouvrir les paramètres")
        store.close()
        with Image.open(store.directory / step["image"]) as saved:
            self.assertEqual(saved.size, (32, 24))
            self.assertEqual(saved.getpixel((1, 1)), self.image.getpixel((1, 1)))
        data = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "Intervention écran")
        self.assertEqual(data["steps"][0]["click"], [12, 7])
        self.assertTrue(data["steps"][0]["timestamp"].endswith("+00:00"))
        report = (store.directory / "rapport.html").read_text(encoding="utf-8")
        self.assertIn('src="captures/000001.png"', report)
        self.assertIn("Clic local", report)
        self.assertNotIn('src="http', report)

    def test_report_escapes_all_user_provided_text(self):
        store = SessionStore(self.root, '<script>alert("x")</script>')
        store.save(self.image, '<img src="evil">', annotation='<script>network()</script> & "note"')
        report = (store.directory / "rapport.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", report)
        self.assertNotIn('<img src="evil">', report)
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("&amp;", report)

    def test_names_cannot_escape_output_and_sessions_are_unique(self):
        first = SessionStore(self.root, "../../CON:/ intervention")
        second = SessionStore(self.root, "../../CON:/ intervention")
        self.assertEqual(first.directory.parent, self.root)
        self.assertNotEqual(first.directory, second.directory)
        self.assertNotIn("..", first.directory.name)

    def test_chronology_trigger_and_custom_timestamps_are_preserved(self):
        store = SessionStore(self.root, "Ordre")
        timestamp = datetime(2026, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
        store.save(self.image, "initial", timestamp=timestamp)
        store.save(self.image, "visual_change", annotation="Écran suivant")
        data = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual([s["index"] for s in data["steps"]], [1, 2])
        self.assertTrue(data["steps"][0]["timestamp"].startswith("2026-01-03T12:00:00"))
        report = (store.directory / "rapport.html").read_text(encoding="utf-8")
        self.assertLess(report.index("Étape 1"), report.index("Étape 2"))
        self.assertIn("Changement visuel détecté", report)

    def test_click_count_is_saved_and_labels_the_report(self):
        store = SessionStore(self.root, "Double-clic")
        store.save(self.image, "local_click", (3, 4), "Double-clic gauche local.", click_count=2)
        store.save(self.image, "visual_change")
        data = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual([step["click_count"] for step in data["steps"]], [2, 0])
        report = (store.directory / "rapport.html").read_text(encoding="utf-8")
        self.assertIn("Étape 1 — Double-clic local", report)
        self.assertIn("Étape 2 — Changement visuel détecté", report)

    def test_empty_session_report_and_repeated_close(self):
        store = SessionStore(self.root, "")
        store.close()
        store.close()
        data = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "Intervention")
        self.assertEqual(data["steps"], [])

    def test_failed_png_write_reports_error_without_adding_step(self):
        store = SessionStore(self.root, "Disque plein")
        with patch.object(Image.Image, "save", side_effect=OSError("disque plein")):
            with self.assertRaisesRegex(StorageError, "non enregistrée"):
                store.save(self.image, "click")
        self.assertEqual(store.steps, [])
        self.assertEqual(list((store.directory / "captures").iterdir()), [])

    def test_failed_metadata_write_retains_image_and_can_be_retried(self):
        store = SessionStore(self.root, "Rapport")
        with patch("stepwatch.storage._atomic_text", side_effect=OSError("verrouillé")):
            with self.assertRaisesRegex(StorageError, "Capture conservée"):
                store.save(self.image, "click")
        self.assertTrue((store.directory / "captures/000001.png").is_file())
        self.assertEqual(len(store.steps), 1)
        store.close()
        data = json.loads((store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["steps"]), 1)

    def test_windows_sharing_violation_is_retried_then_reported(self):
        store = SessionStore(self.root, "Verrou")
        real_replace = os.replace
        calls = []

        def locked_once(source, destination):
            calls.append(Path(destination).name)
            if len(calls) == 1:
                raise PermissionError(13, "fichier utilisé par un autre processus")
            return real_replace(source, destination)

        with patch("stepwatch.storage._REPLACE_ATTEMPTS", 3), \
                patch("stepwatch.storage._REPLACE_DELAY", 0), \
                patch("stepwatch.storage.os.replace", side_effect=locked_once):
            store.save(self.image, "click")
        self.assertEqual(calls[:2], ["000001.png", "000001.png"])
        self.assertTrue((store.directory / "captures/000001.png").is_file())
        self.assertEqual(list(store.directory.rglob(".stepwatch-*")), [])

        with patch("stepwatch.storage._REPLACE_ATTEMPTS", 3), \
                patch("stepwatch.storage._REPLACE_DELAY", 0), \
                patch("stepwatch.storage.os.replace", side_effect=PermissionError(13, "verrou")) as replace:
            with self.assertRaisesRegex(StorageError, "non enregistrée"):
                store.save(self.image, "click")
        self.assertEqual(replace.call_count, 3)
        self.assertEqual(len(store.steps), 1)
        self.assertEqual(list(store.directory.rglob(".stepwatch-*")), [])

    def test_report_failure_after_png_carries_the_saved_step(self):
        store = SessionStore(self.root, "Rapport")
        with patch("stepwatch.storage._atomic_text", side_effect=OSError("verrouillé")):
            with self.assertRaises(ReportWriteError) as caught:
                store.save(self.image, "click", annotation="Étape")
        self.assertEqual(caught.exception.step["image"], "captures/000001.png")
        self.assertEqual(caught.exception.step["annotation"], "Étape")
        self.assertNotIn("close()", str(caught.exception))

    def test_invalid_output_is_actionable(self):
        output = self.root / "file.txt"
        output.write_text("x")
        with self.assertRaisesRegex(StorageError, "Création de l'intervention impossible"):
            SessionStore(output, "Test")

    def test_naive_timestamps_are_rejected_before_writing(self):
        store = SessionStore(self.root, "Date")
        with self.assertRaisesRegex(ValueError, "fuseau"):
            store.save(self.image, "click", timestamp=datetime(2026, 1, 1))
        self.assertEqual(store.steps, [])


class StandaloneReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = SessionStore(self.root, '<Poste "client">')
        self.store.save(Image.new("RGB", (8, 6), "red"), "initial", annotation="<b>Début</b>")
        self.store.save(Image.new("RGB", (8, 6), "blue"), "local_click", (2, 3),
                        "Double-clic gauche local.", click_count=2)
        self.store.close()

    def make_symlink(self, link, target, *, target_is_directory=False):
        """Exercise Windows links too when the runner has the required privilege."""
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Privilège Windows de création de liens symboliques absent (1314).")
            raise

    def test_single_file_embeds_each_png_byte_for_byte(self):
        path = export_standalone(self.store.directory)
        self.assertEqual(path.name, "rapport-autonome.html")
        report = path.read_text(encoding="utf-8")
        sources = re.findall(r'src="data:image/png;base64,([^"]+)"', report)
        self.assertEqual(len(sources), 2)
        for step, source in zip(self.store.steps, sources):
            self.assertEqual(base64.b64decode(source),
                             (self.store.directory / step["image"]).read_bytes())
        self.assertNotIn('src="captures/', report)
        self.assertNotIn("<a href", report)
        self.assertIn("&lt;b&gt;Début&lt;/b&gt;", report)
        self.assertIn("&lt;Poste &quot;client&quot;&gt;", report)
        self.assertIn("Étape 2 — Double-clic local", report)
        self.assertIn("img-src 'self' data:", report)
        self.assertEqual(list(self.store.directory.glob(".stepwatch-*")), [])

    def test_only_pngs_inside_captures_are_embedded(self):
        Image.new("RGB", (2, 2)).save(self.root / "secret.png")
        steps_file = self.store.directory / "steps.json"
        metadata = json.loads(steps_file.read_text(encoding="utf-8"))
        for image in ("../secret.png", "captures/../steps.json", str(self.root / "secret.png")):
            metadata["steps"][0]["image"] = image
            steps_file.write_text(json.dumps(metadata), encoding="utf-8")
            with self.subTest(image=image), \
                    self.assertRaisesRegex(StorageError, "hors du dossier captures"):
                export_standalone(self.store.directory)
        self.assertFalse((self.store.directory / "rapport-autonome.html").exists())
        self.assertEqual(list(self.store.directory.glob(".stepwatch-*")), [])

    def test_symbolic_link_out_of_the_session_is_refused(self):
        outside = self.root / "outside.png"
        Image.new("RGB", (2, 2)).save(outside)
        capture = self.store.directory / "captures" / "000001.png"
        capture.unlink()
        self.make_symlink(capture, outside)
        with self.assertRaisesRegex(StorageError, "hors du dossier captures"):
            export_standalone(self.store.directory)

    def test_absolute_inside_capture_and_noncanonical_paths_are_refused(self):
        steps_file = self.store.directory / "steps.json"
        metadata = json.loads(steps_file.read_text(encoding="utf-8"))
        paths = (
            str(self.store.directory / "captures" / "000001.png"),
            "captures/./000001.png", "captures/../captures/000001.png",
            "captures//000001.png", "./captures/000001.png",
            r"captures\000001.png", "captures/000001.png:secret.png",
            "captures/000001\x00.png",
        )
        for image in paths:
            metadata["steps"][0]["image"] = image
            steps_file.write_text(json.dumps(metadata), encoding="utf-8")
            with self.subTest(image=image), self.assertRaisesRegex(StorageError, "hors du dossier captures"):
                export_standalone(self.store.directory)
        self.assertEqual(list(self.store.directory.glob(".stepwatch-*")), [])

    def test_capture_directory_symlink_outside_the_session_is_refused(self):
        captures = self.store.directory / "captures"
        outside = self.root / "outside-captures"
        captures.rename(outside)
        self.make_symlink(captures, outside, target_is_directory=True)
        with self.assertRaisesRegex(StorageError, "hors du dossier captures"):
            export_standalone(self.store.directory)
        self.assertFalse((self.store.directory / "rapport-autonome.html").exists())
        self.assertEqual(list(self.store.directory.glob(".stepwatch-*")), [])

    def test_capture_symlink_to_another_capture_is_refused(self):
        captures = self.store.directory / "captures"
        first = captures / "000001.png"
        first.unlink()
        self.make_symlink(first, captures / "000002.png")
        with self.assertRaisesRegex(StorageError, "hors du dossier captures"):
            export_standalone(self.store.directory)

    def test_failed_export_preserves_the_previous_standalone_report(self):
        previous = export_standalone(self.store.directory)
        original = previous.read_bytes()
        steps_file = self.store.directory / "steps.json"
        metadata = json.loads(steps_file.read_text(encoding="utf-8"))
        metadata["steps"][0]["image"] = "../secret.png"
        steps_file.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(StorageError):
            export_standalone(self.store.directory)
        self.assertEqual(previous.read_bytes(), original)
        self.assertEqual(list(self.store.directory.glob(".stepwatch-*")), [])

    def test_missing_or_corrupt_capture_names_the_step(self):
        capture = self.store.directory / "captures" / "000002.png"
        capture.write_bytes(b"pas une image")
        with self.assertRaisesRegex(StorageError, "Étape 2 : 000002.png n'est pas une image PNG"):
            export_standalone(self.store.directory)
        capture.unlink()
        with self.assertRaisesRegex(StorageError, "Étape 2 : capture illisible"):
            export_standalone(self.store.directory)
        (self.store.directory / "steps.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(StorageError, "steps.json impossible"):
            export_standalone(self.store.directory)

    def test_reports_show_local_time_and_json_keeps_utc(self):
        winter_paris = timezone(timedelta(hours=1))
        self.assertEqual(_local_time("2026-11-07T18:11:14.694+00:00", winter_paris),
                         "07/11/2026 19:11:14 (UTC+01:00)")
        self.assertEqual(_local_time("pas une date"), "pas une date")
        report = (self.store.directory / "rapport.html").read_text(encoding="utf-8")
        self.assertIn(f'datetime="{self.store.steps[0]["timestamp"]}"', report)
        self.assertRegex(report, r"\d\d/\d\d/\d{4} \d\d:\d\d:\d\d \(UTC[+-]\d\d:\d\d\)")
        data = json.loads((self.store.directory / "steps.json").read_text(encoding="utf-8"))
        self.assertTrue(data["steps"][0]["timestamp"].endswith("+00:00"))


class ShareXImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "sharex"
        self.source.mkdir()
        self.output = self.root / "output"

    def tearDown(self):
        self.temporary.cleanup()

    def make_image(self, name, mtime, color):
        path = self.source / name
        Image.new("RGB", (12, 12), color).save(path)
        os.utime(path, (mtime, mtime))
        return path

    def test_import_copies_sorts_and_preserves_sources(self):
        newest = self.make_image("a.png", 2000, "red")
        oldest = self.make_image("z.png", 1000, "blue")
        tied = self.make_image("b.png", 2000, "green")
        (self.source / "notes.txt").write_text("à conserver")
        original = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                    for p in self.source.iterdir()}
        destination = import_sharex(self.source, self.output)
        data = json.loads((destination / "steps.json").read_text(encoding="utf-8"))
        self.assertEqual([s["annotation"] for s in data["steps"]],
                         ["Source : z.png", "Source : a.png", "Source : b.png"])
        self.assertEqual([s["trigger"] for s in data["steps"]], ["import_sharex"] * 3)
        for source in (newest, oldest, tied):
            self.assertEqual(original[source.name],
                             (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns))
        self.assertTrue((self.source / "notes.txt").is_file())
        self.assertEqual(data["steps"][0]["timestamp"], "1970-01-01T00:16:40.000+00:00")

    def test_second_import_creates_distinct_session(self):
        self.make_image("capture.png", 1000, "white")
        first = import_sharex(self.source, self.output)
        second = import_sharex(self.source, self.output)
        self.assertNotEqual(first, second)
        self.assertTrue((first / "captures/000001.png").exists())

    def test_corrupt_image_reports_source_and_retains_partial_output(self):
        self.make_image("valid.png", 1000, "white")
        invalid = self.source / "corrupt.png"
        invalid.write_bytes(b"not an image")
        os.utime(invalid, (2000, 2000))
        with self.assertRaisesRegex(StorageError, "corrupt.png"):
            import_sharex(self.source, self.output)
        directories = list(self.output.iterdir())
        self.assertEqual(len(directories), 1)
        self.assertTrue((directories[0] / "captures/000001.png").exists())
        self.assertTrue(invalid.exists())

    def test_oversized_image_reports_source_and_partial_output(self):
        self.make_image("valid.png", 1000, "white")
        huge = self.source / "huge.png"
        Image.new("RGB", (40, 40), "white").save(huge)
        os.utime(huge, (2000, 2000))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 400):
            with self.assertRaisesRegex(StorageError, "huge.png.*conservées"):
                import_sharex(self.source, self.output)
        (directory,) = list(self.output.iterdir())
        self.assertTrue((directory / "captures/000001.png").exists())

    def test_missing_or_empty_source_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "n'existe pas"):
            import_sharex(self.root / "missing", self.output)
        with self.assertRaisesRegex(ValueError, "aucune image"):
            import_sharex(self.source, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
