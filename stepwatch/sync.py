"""Synchronisation d'un guide vers un serveur de fichiers.

Trois destinations :

- ``webdav`` : Nextcloud, ownCloud, NAS Synology/QNAP… en HTTPS (certificat
  vérifié ; HTTP accepté seulement vers la machine locale) ;
- ``folder`` : dossier déjà accessible, par exemple ``\\\\nas\\partage`` sous
  Windows ou un partage monté sous Linux ;
- ``sftp`` : serveur SSH. L'empreinte d'un serveur inconnu doit être acceptée
  par l'utilisateur ; une empreinte différente de celle enregistrée est refusée.

Le guide est envoyé dans ``<destination>/<dossier du guide>/`` : captures
d'abord, rapports ensuite, chaque fichier sous un nom temporaire puis renommé,
pour qu'un rapport ne référence jamais une capture absente. Une capture déjà
présente avec la même taille n'est pas renvoyée ; les rapports, petits, le sont
toujours. Les captures des étapes supprimées (``captures/retirees/``) ne sont
jamais envoyées, et celles qui ne sont plus dans le guide sont retirées du
serveur. Les mots de passe restent dans le coffre du système (``keyring``),
jamais dans settings.json.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import posixpath
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

LOGGER = logging.getLogger(__name__)
KEYRING_SERVICE = "StepWatch"
TIMEOUT = 30
TEMP_SUFFIX = ".stepwatch-envoi"
#: Envoyés en dernier, dans cet ordre : ils référencent les captures.
REPORTS = ("steps.json", "rapport.md", "rapport-autonome.html", "rapport.html")
EXCLUDED_DIRECTORIES = {"retirees"}
KINDS = {"webdav": "Nextcloud ou WebDAV", "folder": "Dossier réseau (NAS)", "sftp": "Serveur SFTP"}
PROPFIND_LIMIT = 5_000_000
_DAV = "{DAV:}"


class SyncError(RuntimeError):
    """Message affichable tel quel."""


class UnknownHostKey(SyncError):
    """Serveur SFTP jamais vu : son empreinte doit être acceptée par l'utilisateur."""

    def __init__(self, host: str, port: int, key_type: str, fingerprint: str, key):
        super().__init__(f"Serveur SFTP inconnu {host}:{port} — empreinte {key_type} {fingerprint}")
        self.host, self.port, self.key_type, self.fingerprint, self.key = host, port, key_type, fingerprint, key


@dataclass(frozen=True)
class SyncConfig:
    kind: str
    url: str = ""          # webdav : URL du dossier de destination
    folder: str = ""       # folder : chemin du dossier réseau
    host: str = ""         # sftp
    port: int = 22
    user: str = ""
    remote: str = ""       # sftp : dossier distant

    @property
    def label(self) -> str:
        if self.kind == "webdav":
            return self.url
        if self.kind == "folder":
            return self.folder
        return f"sftp://{self.user + '@' if self.user else ''}{self.host}:{self.port}/{self.remote.lstrip('/')}"

    @property
    def secret_key(self) -> str:
        """Identifiant du mot de passe dans le coffre du système."""
        if self.kind == "webdav":
            return f"webdav:{self.user}@{self.url}"
        return f"sftp:{self.user}@{self.host}:{self.port}"

    @property
    def needs_password(self) -> bool:
        return self.kind == "webdav"


def config_from_preferences(values: dict) -> SyncConfig | None:
    kind = values.get("sync_kind", "")
    if kind not in KINDS:
        return None
    try:
        port = int(str(values.get("sync_port") or "22"))
    except ValueError:
        port = 22
    return SyncConfig(kind=kind, url=values.get("sync_url", "").strip(), folder=values.get("sync_folder", "").strip(),
                      host=values.get("sync_host", "").strip(), port=port, user=values.get("sync_user", "").strip(),
                      remote=values.get("sync_remote", "").strip())


def validate(config: SyncConfig) -> None:
    """Lève SyncError si la configuration est incomplète ou non sûre."""
    if config.kind == "webdav":
        parsed = urllib.parse.urlsplit(config.url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise SyncError("Adresse WebDAV : HTTPS obligatoire (https://…), pour protéger les captures en transit.")
        if not parsed.hostname:
            raise SyncError("Adresse WebDAV incomplète, par exemple "
                            "https://cloud.exemple.fr/remote.php/dav/files/utilisateur/StepWatch")
        if not config.user:
            raise SyncError("Indiquez l’utilisateur WebDAV.")
    elif config.kind == "folder":
        if not config.folder:
            raise SyncError("Indiquez le dossier réseau, par exemple \\\\nas\\interventions.")
    elif config.kind == "sftp":
        if not config.host or not config.user:
            raise SyncError("Indiquez le serveur et l’utilisateur SFTP.")
        if not 0 < config.port < 65536:
            raise SyncError("Port SFTP invalide.")
    else:
        raise SyncError("Aucune destination de synchronisation configurée.")


# ----- Coffre du système --------------------------------------------------------------


def get_password(config: SyncConfig) -> str | None:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, config.secret_key)
    except Exception as exc:  # Pas de coffre (session sans trousseau) : demander à l'utilisateur.
        LOGGER.info("Coffre du système indisponible : %s", exc)
        return None


def set_password(config: SyncConfig, password: str) -> bool:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, config.secret_key, password)
        return True
    except Exception as exc:
        LOGGER.warning("Mot de passe non enregistré dans le coffre du système : %s", exc)
        return False


def delete_password(config: SyncConfig) -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, config.secret_key)
    except Exception:
        pass


# ----- Fichiers à envoyer -----------------------------------------------------------------


@dataclass(frozen=True)
class LocalFile:
    relative: str  # Chemin POSIX relatif au dossier du guide.
    path: Path
    size: int


def local_files(directory: Path) -> list[LocalFile]:
    """Captures (sauf retirées) puis rapports ; fichiers temporaires et cachés exclus."""
    captures, reports, others = [], [], []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(directory)
        parts = relative.parts
        if any(part.startswith(".") for part in parts) or EXCLUDED_DIRECTORIES & set(parts[:-1]):
            continue
        if path.name.endswith((".tmp", TEMP_SUFFIX)):
            continue
        item = LocalFile(relative.as_posix(), path, path.stat().st_size)
        if parts[0] == "captures":
            captures.append(item)
        elif path.name in REPORTS and len(parts) == 1:
            reports.append(item)
        else:
            others.append(item)
    reports.sort(key=lambda item: REPORTS.index(item.relative))
    return captures + others + reports


# ----- Destinations -------------------------------------------------------------------------


class Target:
    """Interface commune : chemins POSIX relatifs à la racine de destination."""

    def ensure_directory(self, relative: str) -> None: ...
    def size(self, relative: str) -> int | None: ...
    def put(self, local: Path, relative: str) -> None: ...
    def list_files(self, relative: str) -> list[str]: ...

    def list_sizes(self, relative: str) -> dict[str, int]:
        """Fichiers d'un dossier distant et leur taille (un seul aller-retour réseau)."""
        return {name: self.size(f"{relative}/{name}") or 0 for name in self.list_files(relative)}
    def delete(self, relative: str) -> None: ...
    def check(self) -> None: ...

    def close(self) -> None:
        pass


class FolderTarget(Target):
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, relative: str) -> Path:
        return self.root.joinpath(*PurePosixPath(relative).parts)

    def check(self) -> None:
        if not self.root.is_dir():
            raise SyncError(f"Dossier réseau inaccessible : {self.root}")
        probe = self.root / f".stepwatch-test{TEMP_SUFFIX}"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise SyncError(f"Écriture impossible dans {self.root} : {exc}") from exc

    def ensure_directory(self, relative: str) -> None:
        self._path(relative).mkdir(parents=True, exist_ok=True)

    def size(self, relative: str) -> int | None:
        try:
            return self._path(relative).stat().st_size
        except OSError:
            return None

    def put(self, local: Path, relative: str) -> None:
        target = self._path(relative)
        temporary = target.with_name(target.name + TEMP_SUFFIX)
        shutil.copyfile(local, temporary)
        os.replace(temporary, target)

    def list_files(self, relative: str) -> list[str]:
        return list(self.list_sizes(relative))

    def list_sizes(self, relative: str) -> dict[str, int]:
        folder = self._path(relative)
        if not folder.is_dir():
            return {}
        return {entry.name: entry.stat().st_size for entry in folder.iterdir() if entry.is_file()}

    def delete(self, relative: str) -> None:
        self._path(relative).unlink(missing_ok=True)


class WebDavTarget(Target):
    def __init__(self, url: str, user: str, password: str, opener=None):
        parts = urllib.parse.urlsplit(url)
        # Les espaces et accents saisis dans l'adresse sont encodés une seule fois.
        path = urllib.parse.quote(urllib.parse.unquote(parts.path).rstrip("/"), safe="/@:+,;=") + "/"
        self.base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        self._auth = f"Basic {token}"
        self._opener = opener
        self._known_directories: set[str] = set()

    def _url(self, relative: str, directory: bool = False) -> str:
        quoted = "/".join(urllib.parse.quote(part) for part in PurePosixPath(relative).parts) if relative else ""
        return self.base + quoted + ("/" if directory and quoted else "")

    def _request(self, method: str, url: str, *, data=None, headers=None, length: int | None = None):
        from .updater import USER_AGENT, ssl_context

        request = urllib.request.Request(url, data=data, method=method,
                                         headers={"Authorization": self._auth, "User-Agent": USER_AGENT,
                                                  **(headers or {})})
        if length is not None:
            request.add_header("Content-Length", str(length))
        try:
            if self._opener is not None:
                return self._opener(request, timeout=TIMEOUT)
            return urllib.request.urlopen(request, timeout=TIMEOUT, context=ssl_context())
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 401:
                raise SyncError("WebDAV : identifiant ou mot de passe refusé (401). Avec Nextcloud, utilisez un "
                                "mot de passe d’application.") from exc
            if exc.code == 403:
                raise SyncError(f"WebDAV : accès refusé (403) à {url}.") from exc
            if exc.code == 507:
                raise SyncError("WebDAV : espace de stockage insuffisant sur le serveur (507).") from exc
            raise
        except urllib.error.URLError as exc:
            raise SyncError(f"Serveur WebDAV injoignable : {exc.reason}") from exc

    def _propfind(self, url: str, depth: str) -> dict[str, tuple[bool, int]]:
        """Entrées (nom décodé → (dossier ?, taille)) d'un PROPFIND."""
        body = (b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
                b'<d:getcontentlength/></d:prop></d:propfind>')
        with self._request("PROPFIND", url, data=body,
                           headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"}) as response:
            payload = response.read(PROPFIND_LIMIT + 1)
        if len(payload) > PROPFIND_LIMIT or b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
            raise SyncError("Réponse WebDAV refusée (trop volumineuse ou avec déclarations XML).")
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise SyncError(f"Réponse WebDAV illisible : {exc}") from exc
        entries: dict[str, tuple[bool, int]] = {}
        base_path = urllib.parse.urlsplit(url).path.rstrip("/")
        for response_element in root.findall(f"{_DAV}response"):
            href = response_element.findtext(f"{_DAV}href") or ""
            path = urllib.parse.unquote(urllib.parse.urlsplit(href).path).rstrip("/")
            name = "" if path == urllib.parse.unquote(base_path) else posixpath.basename(path)
            is_directory = response_element.find(f".//{_DAV}resourcetype/{_DAV}collection") is not None
            length = response_element.findtext(f".//{_DAV}getcontentlength") or "0"
            entries[name] = (is_directory, int(length) if length.isdigit() else 0)
        return entries

    def check(self) -> None:
        try:
            self._propfind(self.base, "0")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.ensure_directory("")
                return
            raise SyncError(f"WebDAV : réponse inattendue ({exc.code}) pour {self.base}.") from exc

    def ensure_directory(self, relative: str) -> None:
        # Crée chaque niveau manquant, racine comprise (MKCOL sur un dossier existant : 405).
        parts = PurePosixPath(relative).parts if relative else ()
        for depth in range(0, len(parts) + 1):
            current = "/".join(parts[:depth])
            if current in self._known_directories:
                continue
            url = self._url(current, directory=True) if current else self.base
            try:
                self._request("MKCOL", url).close()
            except urllib.error.HTTPError as exc:
                if exc.code not in (405, 409, 301):
                    raise SyncError(f"WebDAV : création du dossier {current or '/'} impossible ({exc.code}).") from exc
            self._known_directories.add(current)

    def size(self, relative: str) -> int | None:
        try:
            entries = self._propfind(self._url(relative), "0")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise SyncError(f"WebDAV : lecture de {relative} impossible ({exc.code}).") from exc
        entry = next(iter(entries.values()), None)
        return None if entry is None or entry[0] else entry[1]

    def list_files(self, relative: str) -> list[str]:
        return list(self.list_sizes(relative))

    def list_sizes(self, relative: str) -> dict[str, int]:
        try:
            entries = self._propfind(self._url(relative, directory=True), "1")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise SyncError(f"WebDAV : lecture du dossier {relative} impossible ({exc.code}).") from exc
        return {name: size for name, (is_directory, size) in entries.items() if name and not is_directory}

    def put(self, local: Path, relative: str) -> None:
        temporary = relative + TEMP_SUFFIX
        size = local.stat().st_size
        with open(local, "rb") as handle:
            try:
                self._request("PUT", self._url(temporary), data=handle, length=size,
                              headers={"Content-Type": "application/octet-stream"}).close()
            except urllib.error.HTTPError as exc:
                raise SyncError(f"WebDAV : envoi de {relative} refusé ({exc.code}).") from exc
        try:
            self._request("MOVE", self._url(temporary),
                          headers={"Destination": self._url(relative), "Overwrite": "T"}).close()
        except urllib.error.HTTPError as exc:
            raise SyncError(f"WebDAV : renommage de {relative} refusé ({exc.code}).") from exc

    def delete(self, relative: str) -> None:
        try:
            self._request("DELETE", self._url(relative)).close()
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise SyncError(f"WebDAV : suppression de {relative} refusée ({exc.code}).") from exc


def fingerprint(key) -> str:
    """Empreinte SHA256 au format d'OpenSSH (SHA256:…)."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class SftpTarget(Target):
    def __init__(self, config: SyncConfig, password: str | None, known_hosts: Path, client_factory=None):
        self.config = config
        self.known_hosts = known_hosts
        self._client = None
        self._sftp = None
        self._password = password
        self._client_factory = client_factory
        self.root = PurePosixPath(config.remote or ".")

    def _connect(self):
        if self._sftp is not None:
            return self._sftp
        import paramiko

        client = self._client_factory() if self._client_factory else paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except Exception:
            pass  # ~/.ssh/known_hosts absent ou illisible : seules les empreintes de StepWatch comptent.
        if self.known_hosts.exists():
            client.load_host_keys(str(self.known_hosts))

        host, port = self.config.host, self.config.port

        class Refuse(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, _client, _hostname, key):
                # paramiko transmet « [hôte]:port » hors port 22 : garder l'hôte configuré.
                raise UnknownHostKey(host, port, key.get_name(), fingerprint(key), key)

        client.set_missing_host_key_policy(Refuse())
        try:
            client.connect(self.config.host, port=port, username=self.config.user, password=self._password or None,
                           timeout=TIMEOUT, banner_timeout=TIMEOUT, auth_timeout=TIMEOUT,
                           allow_agent=True, look_for_keys=True)
        except UnknownHostKey:
            client.close()
            raise
        except paramiko.BadHostKeyException as exc:
            client.close()
            raise SyncError(f"L’empreinte du serveur {self.config.host} a changé : connexion refusée. "
                            "Vérifiez le serveur avant de supprimer l’empreinte enregistrée.") from exc
        except paramiko.AuthenticationException as exc:
            client.close()
            raise SyncError("SFTP : authentification refusée (utilisateur, mot de passe ou clé).") from exc
        except (paramiko.SSHException, OSError) as exc:
            client.close()
            raise SyncError(f"Serveur SFTP injoignable : {exc}") from exc
        self._client = client
        self._sftp = client.open_sftp()
        return self._sftp

    def _path(self, relative: str) -> str:
        return str(self.root / relative) if relative else str(self.root)

    def check(self) -> None:
        self.ensure_directory("")

    def ensure_directory(self, relative: str) -> None:
        sftp = self._connect()
        path = PurePosixPath(self._path(relative))
        built = PurePosixPath(path.anchor) if path.is_absolute() else PurePosixPath()
        for part in path.parts[1:] if path.is_absolute() else path.parts:
            built = built / part
            try:
                sftp.stat(str(built))
            except OSError:
                try:
                    sftp.mkdir(str(built))
                except OSError as exc:
                    raise SyncError(f"SFTP : création du dossier {built} impossible : {exc}") from exc

    def size(self, relative: str) -> int | None:
        try:
            return self._connect().stat(self._path(relative)).st_size
        except OSError:
            return None

    def put(self, local: Path, relative: str) -> None:
        sftp = self._connect()
        target = self._path(relative)
        temporary = target + TEMP_SUFFIX
        try:
            with open(local, "rb") as handle:
                sftp.putfo(handle, temporary, file_size=local.stat().st_size, confirm=True)
            try:
                sftp.posix_rename(temporary, target)
            except OSError:
                try:
                    sftp.remove(target)
                except OSError:
                    pass
                sftp.rename(temporary, target)
        except OSError as exc:
            raise SyncError(f"SFTP : envoi de {relative} impossible : {exc}") from exc

    def list_files(self, relative: str) -> list[str]:
        return list(self.list_sizes(relative))

    def list_sizes(self, relative: str) -> dict[str, int]:
        import stat as stat_module

        try:
            entries = self._connect().listdir_attr(self._path(relative))
        except OSError:
            return {}
        return {entry.filename: entry.st_size or 0 for entry in entries
                if entry.st_mode is None or stat_module.S_ISREG(entry.st_mode)}

    def delete(self, relative: str) -> None:
        try:
            self._connect().remove(self._path(relative))
        except OSError:
            pass

    def close(self) -> None:
        for resource in (self._sftp, self._client):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        self._sftp = self._client = None


def trust_host_key(error: UnknownHostKey, known_hosts: Path) -> None:
    """Enregistre l'empreinte acceptée par l'utilisateur (fichier propre à StepWatch)."""
    import paramiko

    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    keys = paramiko.HostKeys()
    if known_hosts.exists():
        keys.load(str(known_hosts))
    name = error.host if error.port == 22 else f"[{error.host}]:{error.port}"
    keys.add(name, error.key.get_name(), error.key)
    keys.save(str(known_hosts))


def known_hosts_path() -> Path:
    from .settings import default_path

    return default_path().parent / "known_hosts"


def open_target(config: SyncConfig, password: str | None = None, *, opener=None) -> Target:
    validate(config)
    if config.kind == "folder":
        return FolderTarget(Path(config.folder))
    if config.kind == "webdav":
        if not password:
            raise SyncError("Mot de passe WebDAV manquant.")
        return WebDavTarget(config.url, config.user, password, opener=opener)
    return SftpTarget(config, password, known_hosts_path())


# ----- Synchronisation ----------------------------------------------------------------------


@dataclass
class SyncResult:
    sent: int = 0
    unchanged: int = 0
    removed: int = 0
    sent_bytes: int = 0
    destination: str = ""
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.sent} fichier{'s' if self.sent > 1 else ''} envoyé{'s' if self.sent > 1 else ''} "
                 f"({self.sent_bytes / 1e6:.1f} Mo)", f"{self.unchanged} déjà à jour"]
        if self.removed:
            parts.append(f"{self.removed} capture{'s' if self.removed > 1 else ''} d’étape supprimée retirée"
                         f"{'s' if self.removed > 1 else ''} du serveur")
        return " · ".join(parts)


def sync_guide(directory: Path, target: Target, *, progress: Callable[[int, int, str], None] | None = None,
               cancelled: Callable[[], bool] = lambda: False) -> SyncResult:
    """Envoie le guide ``directory`` dans ``<cible>/<nom du dossier>/``."""
    directory = Path(directory)
    if not (directory / "steps.json").is_file():
        raise SyncError(f"{directory} n’est pas un dossier de guide StepWatch (steps.json absent).")
    files = local_files(directory)
    remote_root = directory.name
    result = SyncResult()
    target.ensure_directory(remote_root)
    created = {remote_root}
    listings: dict[str, dict[str, int]] = {}
    for number, item in enumerate(files, 1):
        if cancelled():
            raise SyncError("Synchronisation interrompue.")
        remote = f"{remote_root}/{item.relative}"
        parent = posixpath.dirname(remote)
        if parent not in created:
            target.ensure_directory(parent)
            created.add(parent)
        immutable = item.relative.startswith("captures/")
        if immutable and parent not in listings:
            listings[parent] = target.list_sizes(parent)  # Un aller-retour par dossier, pas par capture.
        if immutable and listings[parent].get(posixpath.basename(remote)) == item.size:
            result.unchanged += 1
        else:
            target.put(item.path, remote)
            result.sent += 1
            result.sent_bytes += item.size
        if progress is not None:
            progress(number, len(files), item.relative)
    # Captures d'étapes supprimées localement : retirées du serveur.
    local_captures = {item.relative.split("/", 1)[1] for item in files if item.relative.startswith("captures/")
                      and item.relative.count("/") == 1}
    for name in listings.get(f"{remote_root}/captures") or target.list_sizes(f"{remote_root}/captures"):
        if name.lower().endswith(".png") and name not in local_captures:
            target.delete(f"{remote_root}/captures/{name}")
            result.removed += 1
    return result


class SyncJob:
    """Synchronisation en arrière-plan ; résultat ou erreur lus par l'interface."""

    def __init__(self, directory: Path, config: SyncConfig, password: str | None,
                 on_event: Callable[[dict], None], *, target_factory=open_target):
        self.directory, self.config, self.password = Path(directory), config, password
        self._on_event = on_event
        self._target_factory = target_factory
        self._cancel = threading.Event()
        self.thread = threading.Thread(target=self._run, name="StepWatch-synchronisation", daemon=False)

    def start(self) -> "SyncJob":
        self.thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    def _run(self) -> None:
        target = None
        try:
            target = self._target_factory(self.config, self.password)
            result = sync_guide(self.directory, target, cancelled=self._cancel.is_set,
                                progress=lambda done, total, name: self._on_event(
                                    {"type": "sync_progress", "done": done, "total": total, "name": name}))
            result.destination = self.config.label
            self._on_event({"type": "sync_done", "directory": str(self.directory), "result": result})
        except UnknownHostKey as exc:
            self._on_event({"type": "sync_host_key", "directory": str(self.directory), "error": exc})
        except SyncError as exc:
            self._on_event({"type": "sync_error", "directory": str(self.directory), "message": str(exc)})
        except Exception as exc:
            LOGGER.exception("Synchronisation en échec")
            self._on_event({"type": "sync_error", "directory": str(self.directory),
                            "message": f"Synchronisation en échec : {exc}"})
        finally:
            if target is not None:
                target.close()


def test_connection(config: SyncConfig, password: str | None) -> str:
    """Vérifie l'accès en écriture ; renvoie un message de réussite ou lève SyncError/UnknownHostKey."""
    target = open_target(config, password)
    try:
        target.check()
    finally:
        target.close()
    return f"Connexion réussie : {config.label}"
