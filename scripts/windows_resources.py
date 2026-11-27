#!/usr/bin/env python3
"""Ressources de l'exécutable Windows : informations de version et icône.

Un exécutable sans éditeur, produit ni version ressemble davantage à un
programme malveillant pour les antivirus et pour l'Explorateur. Ce script
écrit dans ``OUTPUT`` :

- ``version.txt`` : fichier de version PyInstaller (``--version-file``) ;
- ``stepwatch.ico`` : icône multi-tailles dessinée comme l'icône Linux.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VERSION_TEMPLATE = """# UTF-8 — généré par scripts/windows_resources.py
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040C04B0', [
        StringStruct('CompanyName', 'Projet StepWatch'),
        StringStruct('FileDescription', 'StepWatch — documentation des interventions informatiques'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'StepWatch'),
        StringStruct('LegalCopyright', 'Projet StepWatch — licence GNU AGPL v3'),
        StringStruct('OriginalFilename', 'StepWatch.exe'),
        StringStruct('ProductName', 'StepWatch'),
        StringStruct('ProductVersion', '{version}'),
        StringStruct('Comments', 'https://github.com/1002-AZ-2001/StepWatch')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
"""


def version_file(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return VERSION_TEMPLATE.format(version=version, major=major, minor=minor, patch=patch)


def write_resources(output: Path, version: str | None = None) -> tuple[Path, Path]:
    sys.path.insert(0, str(ROOT))
    from scripts.build_deb import project_version  # noqa: E402 - après l'ajout du chemin
    from stepwatch.theme import render_icon

    version = version or project_version()
    output.mkdir(parents=True, exist_ok=True)
    version_path = output / "version.txt"
    version_path.write_text(version_file(version), encoding="utf-8")
    icon_path = output / "stepwatch.ico"
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    render_icon(256).save(icon_path, format="ICO", sizes=[(size, size) for size in sizes])
    return version_path, icon_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "windows")
    parser.add_argument("--version")
    arguments = parser.parse_args(argv)
    for path in write_resources(arguments.output, arguments.version):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
