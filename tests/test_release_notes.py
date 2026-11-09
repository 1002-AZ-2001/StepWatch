"""Each published version needs its own CHANGELOG section."""

from pathlib import Path
import tempfile
import unittest

from scripts.release_notes import changelog_section, release_notes

CHANGELOG = """# Journal des modifications

## Non publié

- Rien encore.

## 0.3.0 — 2026-11-09

- Nouveauté A.
- Nouveauté B.

## 0.2.0 — 2026-11-07

- Ancienne entrée.
"""


class ReleaseNotesTests(unittest.TestCase):
    def test_section_stops_at_the_next_version(self):
        body = changelog_section(CHANGELOG, "0.3.0")
        self.assertEqual(body, "- Nouveauté A.\n- Nouveauté B.")
        self.assertEqual(changelog_section(CHANGELOG, "0.2.0"), "- Ancienne entrée.")

    def test_missing_empty_or_prefix_only_sections_are_refused(self):
        for version in ("0.4.0", "0.3"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                changelog_section(CHANGELOG, version)
        with self.assertRaises(ValueError):
            changelog_section("## 1.0.0\n\n## 0.9.0\n- x\n", "1.0.0")

    def test_notes_list_the_files_of_the_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.3.0"\n', encoding="utf-8")
            (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
            tag, notes = release_notes(root)
        self.assertEqual(tag, "v0.3.0")
        self.assertTrue(notes.startswith("- Nouveauté A."))
        self.assertNotIn("Ancienne entrée", notes)
        for name in ("StepWatch-0.3.0-windows-x64.exe", "StepWatch-0.3.0-windows-x64-portable.zip",
                     "stepwatch_0.3.0_amd64.deb", "/blob/v0.3.0/docs/distribution.md"):
            self.assertIn(name, notes)

    def test_current_project_changelog_covers_its_version(self):
        tag, notes = release_notes()
        self.assertRegex(tag, r"^v\d+\.\d+\.\d+$")
        self.assertTrue(notes.strip())


if __name__ == "__main__":
    unittest.main()
