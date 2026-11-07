#!/usr/bin/env python3
"""Freeze StepWatch and produce an amd64 Debian package without root access.

Build on Ubuntu 22.04 (glibc 2.35) for the supported runtime baseline. The
packaged Linux UI supports ShareX imports and reports; recording needs Windows.
An existing PyInstaller onedir can be reused with --bundle /path/to/StepWatch.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "packaging" / "linux"
DEPENDENCIES = (
    "libx11-6, libxext6, libxrender1, libxss1, libxcb1, "
    "libfontconfig1, libfreetype6, libexpat1, zlib1g"
)


class PackagingError(RuntimeError):
    """An actionable package input or prerequisite error."""


def minimum_glibc() -> str:
    """Conservatively require at least the build host's glibc and 2.35.

    Building on the oldest supported distribution remains necessary for a
    broadly compatible package. Reused bundles must originate from that host
    or an older compatible build; arbitrary prebuilt binaries are not audited.
    """
    implementation, version = platform.libc_ver()
    if implementation != "glibc" or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version):
        raise PackagingError("Version glibc du système de compilation indéterminée.")
    components = tuple(int(value) for value in version.split("."))
    normalized = components + (0,) * (3 - len(components))
    return version if normalized > (2, 35, 0) else "2.35"


def native_tcl_binaries() -> list[str]:
    """Include Tcl/Tk shared libraries shipped with a custom Python prefix.

    Relocatable Python distributions may retain their original LIBDIR in
    sysconfig even after installation elsewhere. base_prefix/lib covers that
    case without relying on a machine-specific runtime path. System-installed
    libraries remain handled by PyInstaller's normal dependency resolution.
    """
    directories = [Path(sys.base_prefix) / "lib", Path(sys.prefix) / "lib"]
    configured = sysconfig.get_config_var("LIBDIR")
    if configured:
        directories.insert(0, Path(configured))
    arguments: list[str] = []
    names: set[str] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for pattern in ("libtcl*.so*", "libtk*.so*"):
            for library in sorted(directory.glob(pattern)):
                if library.is_file() and library.name not in names:
                    arguments.extend(["--add-binary", f"{library}:."])
                    names.add(library.name)
    return arguments


def project_version(root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise PackagingError("La version doit être numérique, au format majeur.mineur.correctif.")
    return version


def validate_bundle(bundle: Path) -> None:
    """Reject special files and links escaping an existing onedir payload."""
    if not bundle.is_dir():
        raise PackagingError(f"Dossier PyInstaller introuvable : {bundle}")
    root = bundle.resolve(strict=True)
    executable = bundle / "StepWatch"
    if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise PackagingError(f"Exécutable StepWatch absent ou non exécutable : {executable}")

    def check(path: Path) -> None:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            # Absolute symlinks refer to the build host after installation;
            # require relative links whose destinations are part of the bundle.
            if Path(os.readlink(path)).is_absolute():
                raise PackagingError(f"Lien absolu refusé dans le bundle : {path}")
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise PackagingError(f"Lien invalide dans le bundle : {path}") from exc
            if not resolved.is_relative_to(root):
                raise PackagingError(f"Lien sortant du bundle refusé : {path}")
        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise PackagingError(f"Fichier spécial refusé dans le bundle : {path}")

    def traversal_error(error: OSError) -> None:
        raise error

    for directory, directories, filenames in os.walk(root, onerror=traversal_error, followlinks=False):
        for name in directories + filenames:
            check(Path(directory) / name)


def _normalize_permissions(root: Path) -> None:
    # Host umask and build-user modes must not make the installed package
    # unreadable. Do not preserve setuid/setgid bits or chmod symlink targets.
    root.chmod(0o755)
    for directory, directories, filenames in os.walk(root, followlinks=False):
        for name in directories + filenames:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            mode = path.stat().st_mode
            path.chmod(0o755 if path.is_dir() or mode & 0o111 else 0o644)


def stage_package(bundle: Path, staging: Path, version: str) -> None:
    """Populate a new package root. Public for archive-layout regression tests."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise PackagingError("Version Debian invalide.")
    validate_bundle(bundle)
    staging.mkdir()
    payload = staging / "opt" / "stepwatch"
    payload.parent.mkdir(parents=True)
    shutil.copytree(bundle, payload, symlinks=True)
    assets = {
        "stepwatch": "usr/bin/stepwatch",
        "stepwatch.desktop": "usr/share/applications/stepwatch.desktop",
        "stepwatch.svg": "usr/share/icons/hicolor/scalable/apps/stepwatch.svg",
    }
    for source, destination in assets.items():
        target = staging / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ASSETS / source, target)
    (staging / "usr/bin/stepwatch").chmod(0o755)
    installed_size = (sum(path.stat().st_size for path in staging.rglob("*")
                          if path.is_file() and not path.is_symlink()) + 1023) // 1024
    control = staging / "DEBIAN" / "control"
    control.parent.mkdir()
    control.write_text(
        f"Package: stepwatch\nVersion: {version}\nArchitecture: amd64\n"
        "Section: utils\nPriority: optional\n"
        "Maintainer: StepWatch project <1002-AZ-2001@users.noreply.github.com>\n"
        "Homepage: https://github.com/1002-AZ-2001/StepWatch\n"
        f"Installed-Size: {installed_size}\nDepends: libc6 (>= {minimum_glibc()}), {DEPENDENCIES}\n"
        "Description: Local ShareX imports and offline intervention reports\n"
        " French desktop interface with local PNG, JSON and HTML reports.\n"
        " Linux supports imports and reports only; recording requires Windows.\n"
        " Python and application dependencies are bundled. No server is required.\n",
        encoding="utf-8",
    )
    _normalize_permissions(staging)


def build_package(bundle: Path | None = None, output: Path = ROOT / "dist") -> Path:
    if sys.platform != "linux":
        raise PackagingError("La création du paquet .deb nécessite Linux.")
    for command in ("dpkg", "dpkg-deb"):
        if shutil.which(command) is None:
            raise PackagingError(f"Commande requise absente : {command}")
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if architecture != "amd64":
        raise PackagingError(f"Architecture non prise en charge : {architecture} (amd64 uniquement).")
    version = project_version()
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stepwatch-deb-") as temporary:
        work = Path(temporary)
        if bundle is None:
            build_environment = os.environ.copy()
            build_environment["PYINSTALLER_CONFIG_DIR"] = str(work / "pyinstaller-cache")
            subprocess.run(
                [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                 "--name", "StepWatch", "--paths", str(ROOT),
                 "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
                 "--specpath", str(work), *native_tcl_binaries(),
                 str(ROOT / "stepwatch" / "__main__.py")],
                cwd=ROOT, check=True, env=build_environment,
            )
            bundle = work / "dist" / "StepWatch"
        else:
            bundle = bundle.expanduser().resolve(strict=True)
        staging = work / "root"
        stage_package(bundle, staging, version)
        filename = f"stepwatch_{version}_amd64.deb"
        candidate = work / filename
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(staging), str(candidate)], check=True)
        destination = output / filename
        # copy + replace works when the output directory and /tmp are on
        # different filesystems and never leaves a partly copied final .deb.
        with tempfile.NamedTemporaryFile(prefix=".stepwatch-", suffix=".deb", dir=output, delete=False) as handle:
            pending = Path(handle.name)
        try:
            shutil.copyfile(candidate, pending)
            pending.chmod(0o644)
            os.replace(pending, destination)
        finally:
            pending.unlink(missing_ok=True)
        return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="réutiliser un dossier PyInstaller StepWatch existant")
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="dossier des paquets .deb générés")
    arguments = parser.parse_args(argv)
    try:
        package = build_package(arguments.bundle, arguments.output)
    except (PackagingError, OSError, subprocess.CalledProcessError, KeyError, tomllib.TOMLDecodeError) as exc:
        parser.exit(1, f"Échec de création du paquet : {exc}\n")
    print(f"Paquet créé : {package}")
    print("Linux : import ShareX et rapports uniquement ; enregistrement réservé à Windows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
