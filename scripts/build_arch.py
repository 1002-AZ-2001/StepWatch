#!/usr/bin/env python3
"""Prepare an Arch Linux package from a StepWatch PyInstaller bundle.

Writes a PKGBUILD and its payload archive in --output; ``makepkg`` then
builds ``stepwatch-VERSION-1-x86_64.pkg.tar.zst``. makepkg refuses to run as
root: the CI uses an unprivileged user in an official archlinux:base-devel
container. The installed tree is the same as the Debian package's.

    python scripts/build_deb.py --keep-bundle dist/StepWatch
    python scripts/build_arch.py --bundle dist/StepWatch --output build/arch
    (cd build/arch && makepkg --nodeps)
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_deb import ROOT, PackagingError, minimum_glibc, project_version, stage_files

#: Arch Linux names of the libraries in the Debian control file.
DEPENDENCIES = ("libx11", "libxext", "libxrender", "libxss", "libxft", "libxcb",
                "fontconfig", "freetype2", "expat", "zlib", "ttf-font")


def _root_owned(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def pkgbuild(version: str, glibc: str, archive: str, digest: str) -> str:
    depends = " ".join(f"'{name}'" for name in (f"glibc>={glibc}", *DEPENDENCIES))
    return f"""# Généré par scripts/build_arch.py : ne pas modifier à la main.
pkgname=stepwatch
pkgver={version}
pkgrel=1
pkgdesc="Import ShareX et rapports d'intervention hors ligne (enregistrement réservé à Windows)"
arch=('x86_64')
url="https://github.com/1002-AZ-2001/StepWatch"
license=('AGPL-3.0-only')
depends=({depends})
optdepends=('xorg-xwayland: affichage dans une session Wayland')
# PyInstaller bundle: stripping its binaries can break the application.
options=('!strip' '!debug')
source=("{archive}")
sha256sums=('{digest}')

package() {{
  cp -a --no-preserve=ownership "$srcdir/root/." "$pkgdir/"
  install -Dm644 "$srcdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}}
"""


def prepare(bundle: Path, output: Path, version: str | None = None,
            glibc: str | None = None) -> Path:
    """Write PKGBUILD and the payload archive in a new or empty ``output``."""
    version = project_version() if version is None else version
    glibc = minimum_glibc() if glibc is None else glibc
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or not re.fullmatch(r"[0-9]+\.[0-9]+", glibc):
        raise PackagingError("Version Arch ou glibc invalide.")
    output = output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise PackagingError(f"Le dossier de sortie n'est pas vide : {output}")
    output.mkdir(parents=True, exist_ok=True)
    archive = f"stepwatch-{version}-root.tar.gz"
    with tempfile.TemporaryDirectory(prefix="stepwatch-arch-") as temporary:
        staging = Path(temporary) / "root"
        stage_files(bundle.expanduser().resolve(strict=True), staging)
        shutil.copyfile(ROOT / "LICENSE", Path(temporary) / "LICENSE")
        with tarfile.open(output / archive, "w:gz") as tar:
            tar.add(staging, arcname="root", filter=_root_owned)
            tar.add(Path(temporary) / "LICENSE", arcname="LICENSE", filter=_root_owned)
    digest = hashlib.sha256((output / archive).read_bytes()).hexdigest()
    (output / "PKGBUILD").write_text(pkgbuild(version, glibc, archive, digest), encoding="utf-8")
    return output / "PKGBUILD"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle", type=Path, required=True, help="dossier PyInstaller StepWatch")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "arch",
                        help="dossier du PKGBUILD (nouveau ou vide)")
    arguments = parser.parse_args(argv)
    try:
        path = prepare(arguments.bundle, arguments.output)
    except (PackagingError, OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        parser.exit(1, f"Préparation du paquet Arch impossible : {exc}\n")
    print(f"PKGBUILD prêt : {path}")
    print("Construire ensuite avec makepkg, en utilisateur non root.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
