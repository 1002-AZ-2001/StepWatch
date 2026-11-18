"""Reject unsafe artifact names and mismatched release versions."""

from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree

from scripts.ci_metadata import ROOT, version_for_build


class CIMetadataTests(unittest.TestCase):
    def test_tag_must_match_package_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
            self.assertEqual(version_for_build(root), "0.2.0")
            self.assertEqual(version_for_build(root, "tag", "v0.2.0"), "0.2.0")
            for tag in ("v0.1.0", "v0.2.0-beta", "v0.2.0;command"):
                with self.subTest(tag=tag), self.assertRaises(ValueError):
                    version_for_build(root, "tag", tag)

    def test_non_numeric_versions_cannot_reach_artifact_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version in ("0.2.0-beta", "../../file", "01.2.3", "1.2.3;command"):
                (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
                with self.subTest(version=version), self.assertRaises(ValueError):
                    version_for_build(root)

    def test_package_version_matches_pyproject(self):
        import stepwatch
        self.assertEqual(stepwatch.__version__, version_for_build())

    def test_windows_manifest_version_matches_pyproject(self):
        manifest = ElementTree.parse(ROOT / "scripts" / "StepWatch.manifest")
        identity = manifest.find("{urn:schemas-microsoft-com:asm.v1}assemblyIdentity")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.get("version"), f"{version_for_build()}.0")


if __name__ == "__main__":
    unittest.main()
