"""Verify the installable archive layout without rebuilding PyInstaller."""

import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_deb.py"
SPEC = importlib.util.spec_from_file_location("stepwatch_build_deb", SCRIPT)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


class DebianPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "StepWatch"
        self.bundle.mkdir()
        self.executable = self.bundle / "StepWatch"
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o700)
        (self.bundle / "_internal").mkdir()
        (self.bundle / "_internal" / "library.so").write_bytes(b"bundled runtime placeholder")

    def tearDown(self):
        self.temporary.cleanup()

    def test_glibc_requirement_is_at_least_baseline_and_build_host(self):
        with patch.object(packaging.platform, "libc_ver", return_value=("glibc", "2.31")):
            self.assertEqual(packaging.minimum_glibc(), "2.35")
        with patch.object(packaging.platform, "libc_ver", return_value=("glibc", "2.41")):
            self.assertEqual(packaging.minimum_glibc(), "2.41")
        with patch.object(packaging.platform, "libc_ver", return_value=("", "")):
            with self.assertRaisesRegex(packaging.PackagingError, "glibc"):
                packaging.minimum_glibc()

    def test_rejects_missing_executable_and_invalid_versions(self):
        self.executable.unlink()
        with self.assertRaisesRegex(packaging.PackagingError, "Exécutable"):
            packaging.validate_bundle(self.bundle)
        with self.assertRaisesRegex(packaging.PackagingError, "Version"):
            packaging.stage_package(self.bundle, self.root / "stage", "0.2.0\nDepends: unsafe")

    @unittest.skipUnless(os.name == "posix", "symlinks and POSIX modes")
    def test_rejects_links_escaping_payload_and_preserves_internal_links(self):
        outside = self.root / "outside.txt"
        outside.write_text("must not be packaged")
        bad = self.bundle / "escape"
        bad.symlink_to("../outside.txt")
        with self.assertRaisesRegex(packaging.PackagingError, "sortant"):
            packaging.validate_bundle(self.bundle)
        bad.unlink()
        (self.bundle / "library.so").symlink_to("_internal/library.so")
        stage = self.root / "stage"
        packaging.stage_package(self.bundle, stage, "0.2.0")
        self.assertEqual(os.readlink(stage / "opt/stepwatch/library.so"), "_internal/library.so")
        self.assertEqual(stat.S_IMODE((stage / "usr/bin/stepwatch").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((stage / "opt/stepwatch/StepWatch").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((stage / "opt/stepwatch/_internal/library.so").stat().st_mode), 0o644)

    def test_build_host_x11_and_font_libraries_are_left_to_the_target_system(self):
        internal = self.bundle / "_internal"
        system = ("libfontconfig.so.1", "libfreetype.so.6", "libX11.so.6", "libXft.so.2",
                  "libxcb-shm.so.0", "libexpat.so.1", "libpng16.so.16", "libbrotlicommon.so.1")
        kept = ("libfreetype-57f805ba.so.6.20.6", "libxcb-9065bb12.so.1.1.0", "libtk8.6.so",
                "libpython3.12.so.1.0", "libstdc++.so.6")
        for name in system + kept:
            (internal / name).write_bytes(b"ELF placeholder")
        stage = self.root / "stage"
        packaging.stage_files(self.bundle, stage)
        staged = {path.name for path in (stage / "opt/stepwatch/_internal").iterdir()}
        self.assertFalse(staged & set(system))
        self.assertTrue(set(kept) <= staged)
        self.assertIn("libxft2", packaging.DEPENDENCIES)

    @unittest.skipUnless(os.name == "posix", "FIFO on POSIX")
    def test_rejects_special_files(self):
        os.mkfifo(self.bundle / "pipe")
        with self.assertRaisesRegex(packaging.PackagingError, "spécial"):
            packaging.validate_bundle(self.bundle)

    @unittest.skipUnless(shutil.which("dpkg-deb") and shutil.which("dpkg"), "Debian package tools")
    def test_deb_metadata_layout_modes_and_payload(self):
        with patch.object(packaging, "project_version", return_value="0.2.0"), \
                patch.object(packaging, "minimum_glibc", return_value="2.35"):
            result = packaging.build_package(self.bundle, self.root / "output")
        self.assertEqual(result.name, "stepwatch_0.2.0_amd64.deb")
        metadata = subprocess.check_output(["dpkg-deb", "--field", str(result)], text=True)
        self.assertIn("Version: 0.2.0", metadata)
        self.assertIn("Architecture: amd64", metadata)
        self.assertIn("libc6 (>= 2.35)", metadata)
        self.assertIn("recording requires Windows", metadata)
        listing = subprocess.check_output(["dpkg-deb", "--contents", str(result)], text=True)
        self.assertIn("root/root", listing)
        self.assertIn("./usr/bin/stepwatch", listing)
        self.assertIn("./usr/share/applications/stepwatch.desktop", listing)
        self.assertIn("./usr/share/icons/hicolor/scalable/apps/stepwatch.svg", listing)
        self.assertNotIn("postinst", listing)
        extracted = self.root / "extracted"
        subprocess.run(["dpkg-deb", "--extract", str(result), str(extracted)], check=True)
        wrapper = extracted / "usr/bin/stepwatch"
        self.assertEqual(wrapper.read_text(), '#!/bin/sh\nexec /opt/stepwatch/StepWatch "$@"\n')
        self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o755)
        self.assertEqual((extracted / "opt/stepwatch/StepWatch").read_bytes(), self.executable.read_bytes())
        self.assertIn("Windows", (extracted / "usr/share/applications/stepwatch.desktop").read_text())


if __name__ == "__main__":
    unittest.main()
