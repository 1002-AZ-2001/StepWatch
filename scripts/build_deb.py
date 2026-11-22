#!/usr/bin/env python3
"""Freeze StepWatch and produce an amd64 Debian package without root access.

Build on Ubuntu 22.04 (glibc 2.35) for the supported runtime baseline.
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
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "packaging" / "linux"
#: Bibliothèques système de l'interface Qt (X11 par xcb, Wayland, polices,
#: OpenGL) et de D-Bus. Noms valables sur Debian 12+ et Ubuntu 22.04+.
DEPENDENCIES = (
    "libx11-6, libx11-xcb1, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, "
    "libxcb-randr0, libxcb-render0, libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, "
    "libxcb-util1, libxcb-xfixes0, libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0, "
    "libwayland-client0, libwayland-cursor0, libegl1, libgl1, libdbus-1-3, "
    "libglib2.0-0t64 | libglib2.0-0, libfontconfig1, libfreetype6, libexpat1, zlib1g"
)
WAYLAND_SUGGESTS = (
    "xwayland, python3-gi, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, "
    "gstreamer1.0-plugins-base, gstreamer1.0-pipewire, pipewire, xdg-desktop-portal"
)

#: X11, Wayland, font, GLib and D-Bus libraries that PyInstaller copies from
#: the build host. They must come from the target system instead: a
#: fontconfig built on Ubuntu 22.04 cannot parse the configuration files of a
#: newer distribution (Arch Linux), and mixed GLib or D-Bus versions break at
#: run time. The packages declare these libraries as dependencies. Libraries
#: vendored by wheels carry a hash in their name (libfreetype-57f805ba.so.6)
#: and are kept.
SYSTEM_LIBRARY = re.compile(
    r"lib(X11|X11-xcb|Xau|Xdmcp|Xext|Xfixes|Xft|Xrandr|Xrender|Xss|xcb(-[a-z]+)*|xkbcommon(-x11)?"
    r"|fontconfig|freetype|expat|png16|brotli(common|dec|enc)|bsd|md"
    r"|glib-2\.0|gthread-2\.0|gobject-2\.0|gio-2\.0|gmodule-2\.0|pcre2-8|dbus-1|systemd|gcrypt|gpg-error"
    r"|lz4|cap|wayland-(client|cursor|egl))\.so(\.[0-9]+)*"
)


#: Greffons Qt conservés (le reste ne sert pas à StepWatch et embarquerait
#: GTK, GLib ou OpenGL de la machine de compilation). Wayland : xdg-shell,
#: décorations côté client (GNOME) ; X11 : xcb ; tests : offscreen.
QT_PLUGINS = {
    "platforms": {"libqxcb.so", "libqwayland.so", "libqoffscreen.so"},
    "platformthemes": {"libqxdgdesktopportal.so"},
    "platforminputcontexts": {"libcomposeplatforminputcontextplugin.so", "libibusplatforminputcontextplugin.so"},
    "imageformats": {"libqsvg.so"},
    "iconengines": {"libqsvgicon.so"},
    "wayland-shell-integration": None,  # None : dossier conservé entièrement.
    "wayland-decoration-client": None,
}
QT_TRANSLATIONS = {"qtbase_fr.qm", "qt_fr.qm"}
_ELF_NEEDED = re.compile(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]")


def _needed(path: Path) -> set[str]:
    output = subprocess.run(["readelf", "-d", "--wide", str(path)], capture_output=True, text=True).stdout
    return set(_ELF_NEEDED.findall(output))


def _is_library(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and re.fullmatch(r"lib.+\.so(\.[0-9]+)*", path.name) is not None


def prune_qt_bundle(bundle: Path) -> list[str]:
    """Retire les greffons et traductions Qt inutiles, puis les bibliothèques devenues orphelines.

    Une bibliothèque n'est retirée que si aucun fichier conservé (exécutable,
    module Python, greffon, autre bibliothèque) ne la déclare en DT_NEEDED.
    Renvoie les chemins retirés, relatifs au paquet.
    """
    removed: list[str] = []
    qt = next(bundle.glob("_internal/PySide6/Qt"), None)
    if qt is None:
        return removed
    plugins = qt / "plugins"
    for directory in sorted(plugins.iterdir()) if plugins.is_dir() else ():
        kept = QT_PLUGINS.get(directory.name, set())
        if kept is None:
            continue
        for plugin in sorted(directory.iterdir()):
            if plugin.name not in kept:
                removed.append(plugin.relative_to(bundle).as_posix())
                plugin.unlink() if plugin.is_file() else shutil.rmtree(plugin)
        if not any(directory.iterdir()):
            directory.rmdir()
    translations = qt / "translations"
    for translation in sorted(translations.glob("*")) if translations.is_dir() else ():
        if translation.name not in QT_TRANSLATIONS:
            removed.append(translation.relative_to(bundle).as_posix())
            translation.unlink()
    # Bibliothèques atteignables depuis l'exécutable, les modules Python et les greffons.
    files = [path for path in bundle.rglob("*") if path.is_file() and not path.is_symlink()]
    plugin_files = {path for path in files if plugins in path.parents}
    libraries = {path.name: path for path in files if _is_library(path) and path not in plugin_files}
    roots = [path for path in files if path in plugin_files or path == bundle / "StepWatch"
             or (path.name not in libraries and ".so" in path.suffixes)]
    roots += [path for name, path in libraries.items() if name.startswith("libpython")]  # Chargée par le lanceur.
    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        for name in _needed(pending.pop()):
            if name in libraries and name not in reachable:
                reachable.add(name)
                pending.append(libraries[name])
    for name, path in sorted(libraries.items()):
        if name not in reachable and not name.startswith("libpython"):
            removed.append(path.relative_to(bundle).as_posix())
            path.unlink()
    for link in [path for path in bundle.rglob("*") if path.is_symlink() and not path.exists()]:
        removed.append(link.relative_to(bundle).as_posix())  # Lien vers une bibliothèque retirée.
        link.unlink()
    return removed


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


def stage_files(bundle: Path, staging: Path) -> None:
    """Installed tree shared by the Debian and Arch Linux packages.

    /opt/stepwatch (PyInstaller onedir), /usr/bin/stepwatch, desktop entry and
    icon, with normalised permissions. No package-manager metadata.
    """
    validate_bundle(bundle)
    staging.mkdir()
    payload = staging / "opt" / "stepwatch"
    payload.parent.mkdir(parents=True)
    shutil.copytree(bundle, payload, symlinks=True)
    for directory, _, filenames in os.walk(payload, followlinks=False):
        for name in filenames:
            if SYSTEM_LIBRARY.fullmatch(name):
                (Path(directory) / name).unlink()
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
    _normalize_permissions(staging)


def stage_package(bundle: Path, staging: Path, version: str) -> None:
    """Populate a new package root. Public for archive-layout regression tests."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise PackagingError("Version Debian invalide.")
    stage_files(bundle, staging)
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
        f"Suggests: {WAYLAND_SUGGESTS}\n"
        "Description: Local screen recording and offline intervention reports\n"
        " French desktop interface with local PNG, JSON and HTML reports.\n"
        " Supports ShareX imports and recording on X11. In a Wayland session,\n"
        " the interface uses XWayland and recording uses the desktop ScreenCast\n"
        " portal with PipeWire and the suggested system packages.\n",
        encoding="utf-8",
    )
    _normalize_permissions(staging)


def freeze(work: Path) -> Path:
    """PyInstaller onedir bundle of StepWatch in ``work``; returns its folder."""
    build_environment = os.environ.copy()
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(work / "pyinstaller-cache")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
         "--name", "StepWatch", "--paths", str(ROOT),
         "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
         "--specpath", str(work), "--add-data", f"{ROOT / 'stepwatch' / '_wayland_portal.py'}:stepwatch",
         "--add-data", f"{ROOT / 'stepwatch' / 'assets'}:stepwatch/assets",
         # Interface Qt : Tk n'est plus utilisé.
         "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
         "--exclude-module", "PySide6.QtNetwork", "--exclude-module", "PySide6.QtDBus",
         str(ROOT / "stepwatch" / "__main__.py")],
        cwd=ROOT, check=True, env=build_environment,
    )
    bundle = work / "dist" / "StepWatch"
    prune_qt_bundle(bundle)
    return bundle


def build_package(bundle: Path | None = None, output: Path = ROOT / "dist",
                  keep_bundle: Path | None = None) -> Path:
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
            bundle = freeze(work)
        else:
            bundle = bundle.expanduser().resolve(strict=True)
        if keep_bundle is not None:
            # Reused by scripts/build_arch.py: one frozen bundle, two packages.
            keep_bundle = keep_bundle.expanduser().resolve()
            if keep_bundle.exists():
                raise PackagingError(f"Dossier déjà présent, non remplacé : {keep_bundle}")
            shutil.copytree(bundle, keep_bundle, symlinks=True)
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
    parser.add_argument("--keep-bundle", type=Path,
                        help="copier aussi le dossier PyInstaller ici (pour le paquet Arch Linux)")
    arguments = parser.parse_args(argv)
    try:
        package = build_package(arguments.bundle, arguments.output, arguments.keep_bundle)
    except (PackagingError, OSError, subprocess.CalledProcessError, KeyError, tomllib.TOMLDecodeError) as exc:
        parser.exit(1, f"Échec de création du paquet : {exc}\n")
    print(f"Paquet créé : {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
