"""Arch Linux PKGBUILD and payload, without PyInstaller or makepkg."""

import hashlib
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts import build_arch
from scripts.build_deb import PackagingError


@unittest.skipUnless(os.name == "posix", "POSIX modes")
class ArchPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "StepWatch"
        (self.bundle / "_internal").mkdir(parents=True)
        executable = self.bundle / "StepWatch"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
        (self.bundle / "_internal" / "library.so").write_bytes(b"bundled runtime placeholder")
        (self.bundle / "library.so").symlink_to("_internal/library.so")

    def test_pkgbuild_declares_dependencies_checksum_and_keeps_binaries_unstripped(self):
        output = self.root / "arch"
        pkgbuild = build_arch.prepare(self.bundle, output, version="0.4.0", glibc="2.35").read_text()
        archive = output / "stepwatch-0.4.0-root.tar.gz"
        self.assertIn("pkgver=0.4.0\npkgrel=1\n", pkgbuild)
        self.assertIn("arch=('x86_64')", pkgbuild)
        self.assertIn("license=('AGPL-3.0-only')", pkgbuild)
        self.assertIn("'glibc>=2.35' 'libx11'", pkgbuild)
        self.assertIn("optdepends=('xorg-xwayland:", pkgbuild)
        self.assertIn("options=('!strip' '!debug')", pkgbuild)
        self.assertIn(f"sha256sums=('{hashlib.sha256(archive.read_bytes()).hexdigest()}')", pkgbuild)
        self.assertIn('cp -a --no-preserve=ownership "$srcdir/root/." "$pkgdir/"', pkgbuild)

    def test_payload_matches_the_debian_layout_and_is_root_owned(self):
        output = self.root / "arch"
        build_arch.prepare(self.bundle, output, version="0.4.0", glibc="2.35")
        with tarfile.open(output / "stepwatch-0.4.0-root.tar.gz") as tar:
            members = {member.name: member for member in tar.getmembers()}
        for name in ("root/opt/stepwatch/StepWatch", "root/usr/bin/stepwatch",
                     "root/usr/share/applications/stepwatch.desktop",
                     "root/usr/share/icons/hicolor/scalable/apps/stepwatch.svg", "LICENSE"):
            self.assertIn(name, members)
        self.assertTrue(all(m.uid == 0 and m.gid == 0 and m.uname == "root" for m in members.values()))
        self.assertEqual(stat.S_IMODE(members["root/opt/stepwatch/StepWatch"].mode), 0o755)
        self.assertEqual(stat.S_IMODE(members["root/usr/bin/stepwatch"].mode), 0o755)
        link = members["root/opt/stepwatch/library.so"]
        self.assertTrue(link.issym())
        self.assertEqual(link.linkname, "_internal/library.so")

    def test_refuses_invalid_versions_and_a_non_empty_output(self):
        with self.assertRaises(PackagingError):
            build_arch.prepare(self.bundle, self.root / "a", version="0.4.0\nsource=x", glibc="2.35")
        with self.assertRaises(PackagingError):
            build_arch.prepare(self.bundle, self.root / "b", version="0.4.0", glibc="2.35'")
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "PKGBUILD").write_text("ancien")
        with self.assertRaisesRegex(PackagingError, "pas vide"):
            build_arch.prepare(self.bundle, occupied, version="0.4.0", glibc="2.35")
        self.assertEqual((occupied / "PKGBUILD").read_text(), "ancien")


if __name__ == "__main__":
    unittest.main()
