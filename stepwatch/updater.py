"""Mises à jour de StepWatch depuis les versions publiées sur GitHub.

Le seul accès réseau de StepWatch. Il n'envoie aucune capture, aucun journal ni
identifiant : une requête à l'API publique des releases, puis le
téléchargement du fichier choisi. La vérification automatique se désactive
dans Paramètres › Général.

Le fichier téléchargé est vérifié avec deux sommes SHA-256 avant toute
installation : celle que GitHub calcule pour chaque fichier publié
(``digest``), et celle du fichier SHA256SUMS de la version. Les deux viennent
de la même version publiée : elles détectent un fichier corrompu ou altéré
en route, pas une version publiée par un tiers qui contrôlerait le dépôt.

Installation selon la forme de StepWatch :

- ``exe`` (Windows, fichier unique) : l'exécutable en cours est renommé en
  ``.old`` (Windows autorise le renommage d'un programme lancé, pas son
  remplacement), le nouveau prend sa place, puis StepWatch redémarre ;
- ``setup`` (Windows, installé par l'installateur) : un script PowerShell attend
  la fermeture de StepWatch, lance le nouvel installateur en mode silencieux
  dans le même dossier, puis relance l'application ;
- ``portable`` (Windows, dossier du ZIP) : l'archive est extraite et vérifiée,
  puis un script PowerShell attend la fermeture de StepWatch, copie les
  fichiers et relance l'application ;
- ``deb`` / ``arch`` : ``pkexec apt-get install`` ou ``pkexec pacman -U``,
  avec le mot de passe administrateur ;
- ``source`` (lancé depuis Python) : rien n'est installé, la page de la
  version est proposée.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from . import __version__

LOGGER = logging.getLogger(__name__)
REPOSITORY = "1002-AZ-2001/StepWatch"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
USER_AGENT = f"StepWatch/{__version__} (+https://github.com/{REPOSITORY})"
TIMEOUT = 15
MAX_DOWNLOAD = 400_000_000
NOTES_LIMIT = 6000
LINUX_COMMAND = "/usr/bin/stepwatch"
LINUX_PAYLOAD = PurePosixPath("/opt/stepwatch")
AFTER_UPDATE = "--after-update"
#: Certificats système connus, si OpenSSL embarqué ne trouve pas les siens.
CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem",
            "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/ca-bundle.pem")
ASSET_PATTERNS = {
    "exe": re.compile(r"StepWatch-[0-9.]+-windows-x64\.exe"),
    "setup": re.compile(r"StepWatch-[0-9.]+-windows-x64-setup\.exe"),
    "portable": re.compile(r"StepWatch-[0-9.]+-windows-x64-portable\.zip"),
    "deb": re.compile(r"stepwatch_[0-9.]+_amd64\.deb"),
    "arch": re.compile(r"stepwatch-[0-9.]+-[0-9]+-x86_64\.pkg\.tar\.zst"),
}
SUMS_FILES = {"exe": "SHA256SUMS-windows.txt", "setup": "SHA256SUMS-windows.txt", "portable": "SHA256SUMS-windows.txt",
              "deb": "SHA256SUMS-linux.txt", "arch": "SHA256SUMS-linux.txt"}
KIND_LABELS = {
    "exe": "exécutable Windows",
    "setup": "installation Windows",
    "portable": "dossier portable Windows",
    "deb": "paquet Debian / Ubuntu",
    "arch": "paquet Arch Linux",
    "source": "installation depuis les sources",
}


class UpdateError(RuntimeError):
    """Message affichable tel quel à l'utilisateur."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    digest: str = ""  # "sha256:<hex>" fourni par GitHub, ou vide.


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    notes: str
    page: str
    assets: tuple[Asset, ...] = field(default_factory=tuple)

    def asset(self, name: str) -> Asset | None:
        return next((asset for asset in self.assets if asset.name == name), None)


@dataclass(frozen=True)
class Installation:
    kind: str  # exe, setup, portable, deb, arch ou source
    path: Path | None = None  # Exécutable (exe) ou dossier (portable).

    @property
    def label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def installable(self) -> bool:
        return self.kind != "source"


# ----- Versions -------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, ...] | None:
    """``v1.2.3`` ou ``1.2.3`` en tuple ; None pour une pré-version ou un texte libre."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


# ----- Réseau ---------------------------------------------------------------------


def ssl_context() -> ssl.SSLContext:
    """Contexte TLS vérifié ; complété par les certificats du système si besoin.

    Python embarqué dans les paquets Linux cherche ses certificats à
    l'emplacement de la machine de compilation : sous Debian ou Arch, ce
    chemin peut ne pas exister.
    """
    context = ssl.create_default_context()
    if sys.platform.startswith("linux") and not os.environ.get("SSL_CERT_FILE"):
        paths = ssl.get_default_verify_paths()
        if not any(path and os.path.exists(path) for path in (paths.cafile, paths.capath)):
            for candidate in CA_FILES:
                if os.path.exists(candidate):
                    context.load_verify_locations(cafile=candidate)
                    break
    return context


def _open(url: str, *, accept: str, opener=None):
    if not url.startswith("https://"):
        raise UpdateError("Adresse de téléchargement refusée (HTTPS obligatoire).")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    if opener is not None:
        return opener(request, timeout=TIMEOUT)
    return urllib.request.urlopen(request, timeout=TIMEOUT, context=ssl_context())


def parse_release(data: dict) -> Release:
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None or data.get("draft") or data.get("prerelease"):
        raise UpdateError(f"Version publiée illisible : « {tag[:40]} ».")
    assets = []
    for item in data.get("assets") or ():
        try:
            assets.append(Asset(name=str(item["name"]), url=str(item["browser_download_url"]),
                                size=int(item.get("size") or 0), digest=str(item.get("digest") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    notes = str(data.get("body") or "").strip()
    if len(notes) > NOTES_LIMIT:
        notes = notes[:NOTES_LIMIT].rstrip() + "\n…"
    version = ".".join(str(part) for part in parse_version(tag))
    return Release(version=version, tag=tag, notes=notes,
                   page=str(data.get("html_url") or RELEASES_PAGE), assets=tuple(assets))


def fetch_latest(opener=None) -> Release:
    """Dernière version publiée (hors brouillons et pré-versions)."""
    try:
        with _open(LATEST_URL, accept="application/vnd.github+json", opener=opener) as response:
            data = json.loads(response.read(2_000_000).decode("utf-8"))
    except UpdateError:
        raise
    except (OSError, ValueError) as exc:
        raise UpdateError(f"Impossible de joindre GitHub : {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("Réponse de GitHub inattendue.")
    return parse_release(data)


# ----- Installation en place ------------------------------------------------------


def detect_installation(*, frozen: bool | None = None, executable: str | None = None,
                        platform: str | None = None, bundle_dir: str | None = None,
                        root: Path = Path("/")) -> Installation:
    """Forme de l'installation en cours, d'après l'exécutable et le gestionnaire de paquets."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    if not frozen:
        return Installation("source")
    exe = Path(executable or sys.executable)
    if platform == "win32":
        bundle = Path(bundle_dir or getattr(sys, "_MEIPASS", exe.parent))
        try:
            inside = bundle.resolve().is_relative_to(exe.parent.resolve())
        except OSError:
            inside = True
        if not inside:
            # Fichier unique : PyInstaller s'extrait dans un dossier temporaire.
            return Installation("exe", exe)
        if (exe.parent / "unins000.exe").exists():
            return Installation("setup", exe.parent)  # Installé par l'installateur Inno Setup.
        return Installation("portable", exe.parent)
    if platform.startswith("linux"):
        try:
            real = exe.resolve() if os.name == "posix" else exe
        except OSError:
            real = exe
        installed = PurePosixPath(real.as_posix()).is_relative_to(LINUX_PAYLOAD)
        if installed:
            if (root / "var/lib/dpkg/info/stepwatch.list").exists():
                return Installation("deb")
            if any((root / "var/lib/pacman/local").glob("stepwatch-[0-9]*")):
                return Installation("arch")
    return Installation("source")


def asset_for(release: Release, installation: Installation) -> Asset | None:
    pattern = ASSET_PATTERNS.get(installation.kind)
    if pattern is None:
        return None
    return next((asset for asset in release.assets if pattern.fullmatch(asset.name)), None)


def _expected_from_sums(text: str, name: str) -> str | None:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def download(release: Release, asset: Asset, directory: Path, *,
             progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] = lambda: False, opener=None) -> Path:
    """Télécharge ``asset`` dans ``directory`` et vérifie ses sommes SHA-256.

    Lève UpdateError si une somme manque, diffère, ou si le transfert échoue.
    Un fichier refusé est effacé.
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+", asset.name):
        raise UpdateError("Nom de fichier refusé.")
    if asset.size > MAX_DOWNLOAD:
        raise UpdateError("Fichier de mise à jour anormalement volumineux.")
    expected = []
    if asset.digest:
        algorithm, _, value = asset.digest.partition(":")
        if algorithm != "sha256" or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise UpdateError("Empreinte GitHub du fichier illisible.")
        expected.append(value.lower())
    sums = next((release.asset(name) for kind, name in SUMS_FILES.items()
                 if ASSET_PATTERNS[kind].fullmatch(asset.name) and release.asset(name)), None)
    if sums is not None:
        try:
            with _open(sums.url, accept="application/octet-stream", opener=opener) as response:
                listed = _expected_from_sums(response.read(100_000).decode("utf-8", "replace"), asset.name)
        except (OSError, ValueError) as exc:
            raise UpdateError(f"Liste des sommes SHA-256 inaccessible : {exc}") from exc
        if listed is None:
            raise UpdateError(f"{asset.name} absent de {sums.name}.")
        expected.append(listed)
    if not expected:
        raise UpdateError("Aucune somme SHA-256 publiée pour ce fichier : installation refusée.")
    if len(set(expected)) != 1:
        raise UpdateError("Les sommes SHA-256 publiées ne concordent pas : installation refusée.")

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / asset.name
    digest = hashlib.sha256()
    received = 0
    try:
        with _open(asset.url, accept="application/octet-stream", opener=opener) as response, \
                open(target, "wb") as handle:
            while True:
                if cancelled():
                    raise UpdateError("Téléchargement annulé.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_DOWNLOAD:
                    raise UpdateError("Fichier de mise à jour anormalement volumineux.")
                digest.update(chunk)
                handle.write(chunk)
                if progress is not None:
                    progress(received, asset.size)
        if asset.size and received != asset.size:
            raise UpdateError(f"Téléchargement incomplet ({received} octets sur {asset.size}).")
        if digest.hexdigest() != expected[0]:
            raise UpdateError("Somme SHA-256 différente de celle publiée : fichier rejeté.")
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement interrompu : {exc}") from exc
    return target


def _check_writable(directory: Path) -> None:
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".stepwatch-update-"):
            pass
    except OSError as exc:
        raise UpdateError(f"Dossier de StepWatch protégé en écriture ({directory}) : "
                          "remplacez le fichier manuellement ou relancez en administrateur.") from exc


def replace_executable(executable: Path, new_file: Path) -> Path:
    """Remplace un exécutable en cours d'exécution ; l'ancien devient ``.old``."""
    _check_writable(executable.parent)
    old = executable.with_name(executable.name + ".old")
    old.unlink(missing_ok=True)
    staged = executable.with_name(executable.name + ".new")
    shutil.copyfile(new_file, staged)
    os.replace(executable, old)  # Autorisé même pendant l'exécution sous Windows.
    try:
        os.replace(staged, executable)
    except OSError:
        os.replace(old, executable)  # Remettre l'ancien en place.
        staged.unlink(missing_ok=True)
        raise
    return executable


def extract_portable(archive: Path, destination: Path) -> Path:
    """Extrait le ZIP portable après contrôle des chemins ; renvoie le dossier StepWatch."""
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if "StepWatch/StepWatch.exe" not in {name.replace("\\", "/") for name in names}:
                raise UpdateError("Archive portable inattendue : StepWatch/StepWatch.exe manquant.")
            for info in bundle.infolist():
                name = info.filename.replace("\\", "/")
                target = (base / name).resolve()
                if not name.startswith("StepWatch/") or not target.is_relative_to(base / "StepWatch"):
                    raise UpdateError(f"Chemin refusé dans l'archive : {info.filename[:80]}")
            bundle.extractall(base)
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"Archive portable illisible : {exc}") from exc
    return base / "StepWatch"


PORTABLE_HELPER = r"""param([int]$WaitFor, [string]$Source, [string]$Target, [string]$Restart)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $WaitFor -Timeout 120 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
$copied = $false
for ($attempt = 0; $attempt -lt 20 -and -not $copied; $attempt++) {
    try {
        Copy-Item -Path (Join-Path $Source '*') -Destination $Target -Recurse -Force
        $copied = $true
    } catch { Start-Sleep -Seconds 1 }
}
if ($Restart) {
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath (Join-Path $Target 'StepWatch.exe') -ArgumentList '--after-update'
}
Remove-Item -LiteralPath (Split-Path -Parent (Split-Path -Parent $Source)) -Recurse -Force -ErrorAction SilentlyContinue
if (-not $copied) { exit 1 }
"""


def start_portable_helper(source: Path, target: Path, *, wait_for: int, restart: bool = True,
                          popen=subprocess.Popen) -> subprocess.Popen:
    """Lance le script qui copie ``source`` sur ``target`` après la fin du processus ``wait_for``."""
    _check_writable(target)
    script = source.parent / "appliquer-mise-a-jour.ps1"
    script.write_text(PORTABLE_HELPER, encoding="utf-8-sig")
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", str(script), "-WaitFor", str(wait_for),
               "-Source", str(source), "-Target", str(target), "-Restart", "1" if restart else ""]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return popen(command, creationflags=flags, close_fds=True, env=_clean_environment())


SETUP_HELPER = r"""param([int]$WaitFor, [string]$Setup, [string]$Target, [string]$Restart)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $WaitFor -Timeout 120 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
$process = Start-Process -FilePath $Setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$Target`"" -Wait -PassThru
if ($Restart) {
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath (Join-Path $Target 'StepWatch.exe') -ArgumentList '--after-update'
}
Remove-Item -LiteralPath (Split-Path -Parent $Setup) -Recurse -Force -ErrorAction SilentlyContinue
exit $process.ExitCode
"""


def start_setup_helper(setup: Path, target: Path, *, wait_for: int, restart: bool = True,
                       popen=subprocess.Popen) -> subprocess.Popen:
    """Lance l'installateur en mode silencieux après la fin du processus ``wait_for``."""
    _check_writable(target)
    script = setup.parent / "installer-mise-a-jour.ps1"
    script.write_text(SETUP_HELPER, encoding="utf-8-sig")
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", str(script), "-WaitFor", str(wait_for),
               "-Setup", str(setup), "-Target", str(target), "-Restart", "1" if restart else ""]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return popen(command, creationflags=flags, close_fds=True, env=_clean_environment())


def package_command(installation: Installation, package: Path) -> list[str]:
    if installation.kind == "deb":
        return ["pkexec", "apt-get", "install", "-y", str(package)]
    if installation.kind == "arch":
        return ["pkexec", "pacman", "-U", "--noconfirm", str(package)]
    raise UpdateError("Installation par paquet impossible ici.")


def install_package(installation: Installation, package: Path, *, run=subprocess.run) -> None:
    if shutil.which("pkexec") is None and run is subprocess.run:
        manual = "sudo apt install" if installation.kind == "deb" else "sudo pacman -U"
        raise UpdateError(f"pkexec introuvable. Installez le paquet téléchargé : {manual} {package}")
    package.chmod(0o644)  # apt lit le paquet avec l'utilisateur _apt.
    result = run(package_command(installation, package), capture_output=True, text=True, timeout=900)
    if result.returncode in (126, 127):
        raise UpdateError("Installation annulée : mot de passe administrateur non fourni.")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
        raise UpdateError(f"Installation du paquet en échec (code {result.returncode}). {detail[0][:300]}")


def _clean_environment() -> dict[str, str]:
    """Environnement d'un nouveau StepWatch indépendant de celui-ci (PyInstaller)."""
    environment = dict(os.environ)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for name in ("LD_LIBRARY_PATH", "LIBPATH"):
        original = environment.pop(f"{name}_ORIG", None)
        if original is not None:
            environment[name] = original
        elif getattr(sys, "frozen", False):
            environment.pop(name, None)
    return environment


def relaunch(command: list[str], *, popen=subprocess.Popen) -> None:
    """Démarre la nouvelle version ; elle attend que celle-ci soit fermée."""
    options: dict = {"close_fds": True, "env": _clean_environment()}
    if sys.platform == "win32":
        options["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        options["start_new_session"] = True
    popen(command, **options)


def apply(installation: Installation, downloaded: Path, *, run=subprocess.run,
          popen=subprocess.Popen, pid: int | None = None) -> list[str] | None:
    """Installe ``downloaded``. Renvoie la commande de redémarrage, ou None si un script s'en charge."""
    if installation.kind == "exe":
        executable = replace_executable(installation.path, downloaded)
        return [str(executable), AFTER_UPDATE]
    if installation.kind == "setup":
        start_setup_helper(downloaded, installation.path, wait_for=os.getpid() if pid is None else pid, popen=popen)
        return None
    if installation.kind == "portable":
        staged = extract_portable(downloaded, downloaded.parent / "extrait")
        start_portable_helper(staged, installation.path, wait_for=os.getpid() if pid is None else pid,
                              popen=popen)
        return None
    if installation.kind in ("deb", "arch"):
        install_package(installation, downloaded, run=run)
        return [LINUX_COMMAND, AFTER_UPDATE]
    raise UpdateError("Mise à jour automatique indisponible pour une installation depuis les sources.")


def cleanup_previous(executable: Path | None = None) -> None:
    """Efface l'exécutable ``.old`` laissé par une mise à jour (Windows, fichier unique)."""
    if executable is None:
        if not getattr(sys, "frozen", False):
            return
        executable = Path(sys.executable)
    for leftover in (executable.with_name(executable.name + ".old"), executable.with_name(executable.name + ".new")):
        for _attempt in range(5):
            try:
                leftover.unlink(missing_ok=True)
                break
            except OSError:
                time.sleep(0.5)  # L'ancien processus termine sa fermeture.


def download_directory() -> Path:
    return Path(tempfile.mkdtemp(prefix="stepwatch-mise-a-jour-"))
