"""Validated artifact version; never interpolate unchecked tag names in shells."""

import os
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def version_for_build(project: Path = ROOT, ref_type: str = "", ref_name: str = "") -> str:
    version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("La version doit être numérique : MAJEUR.MINEUR.CORRECTIF.")
    if ref_type == "tag" and ref_name != f"v{version}":
        raise ValueError(f"Le tag doit être v{version} pour correspondre à pyproject.toml.")
    return version


def main():
    version = version_for_build(ref_type=os.environ.get("GITHUB_REF_TYPE", ""),
                                ref_name=os.environ.get("GITHUB_REF_NAME", ""))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"version={version}\n")
    print(version)


if __name__ == "__main__":
    main()
