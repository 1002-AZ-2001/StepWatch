"""Release notes of the project version, taken from CHANGELOG.md.

A release is refused when the changelog has no section for the version: each
published version must say what changed. Prints the tag (vX.Y.Z) and writes
the notes to --output, or to stdout.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_metadata import ROOT, version_for_build

REPOSITORY = "https://github.com/1002-AZ-2001/StepWatch"


def changelog_section(changelog: str, version: str) -> str:
    """Body of the ``## <version>`` section (heading excluded), stripped."""
    heading = re.compile(rf"^## {re.escape(version)}(?:\s|$).*$", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md ne contient pas de section « ## {version} ».")
    following = re.compile(r"^## ", re.MULTILINE).search(changelog, match.end())
    body = changelog[match.end():following.start() if following else len(changelog)].strip()
    if not body:
        raise ValueError(f"La section « ## {version} » de CHANGELOG.md est vide.")
    return body


def release_notes(project: Path = ROOT) -> tuple[str, str]:
    version = version_for_build(project)
    changelog = (project / "CHANGELOG.md").read_text(encoding="utf-8")
    body = changelog_section(changelog, version)
    notes = f"""{body}

## Fichiers de cette version

| Fichier | Usage |
| --- | --- |
| `StepWatch-{version}-windows-x64.exe` | Windows 10/11 x64, un seul fichier, sans installation de Python. |
| `StepWatch-{version}-windows-x64-portable.zip` | Windows, dossier portable : extraire et conserver le dossier entier. |
| `stepwatch_{version}_amd64.deb` | Debian 12+ / Ubuntu 22.04+ amd64, X11 ou Wayland (XWayland) : import ShareX et rapports. |
| `stepwatch-{version}-1-x86_64.pkg.tar.zst` | Arch Linux x86_64, X11 ou Wayland (XWayland) : import ShareX et rapports. |
| `SHA256SUMS-windows.txt`, `SHA256SUMS-linux.txt` | Sommes de contrôle des fichiers ci-dessus. |

Vérifier un téléchargement : `sha256sum -c SHA256SUMS-linux.txt --ignore-missing`, ou `Get-FileHash` sous PowerShell. Sous Linux, l'enregistrement n'est pas disponible ; `stepwatch --self-test` vérifie l'installation. Les exécutables Windows ne sont pas signés : SmartScreen peut afficher un avertissement.

Installation et vérifications : [guide de distribution]({REPOSITORY}/blob/v{version}/docs/distribution.md) · [validation]({REPOSITORY}/blob/v{version}/docs/validation.md) · [journal complet]({REPOSITORY}/blob/v{version}/CHANGELOG.md).
"""
    return f"v{version}", notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="fichier Markdown des notes")
    arguments = parser.parse_args(argv)
    try:
        tag, notes = release_notes()
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Notes de version impossibles : {exc}\n")
    if arguments.output:
        arguments.output.write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    print(tag, file=sys.stderr if not arguments.output else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
