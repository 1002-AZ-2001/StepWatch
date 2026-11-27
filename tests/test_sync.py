"""Synchronisation des guides : dossier réseau, WebDAV (serveur local de test) et SFTP simulé."""

import base64
import http.server
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from stepwatch import sync
from stepwatch.guide import remove_step
from stepwatch.storage import SessionStore, read_session
from stepwatch.sync import FolderTarget, SyncConfig, SyncError, UnknownHostKey


def make_guide(root: Path, count: int = 3) -> Path:
    store = SessionStore(root, "Guide synchronisé")
    for colour in ("red", "green", "blue")[:count]:
        store.save(Image.new("RGB", (40, 30), colour), "local_click", click=(4, 4), annotation=f"Clic {colour}.")
    store.close()
    return store.directory


class LocalFilesTests(unittest.TestCase):
    def test_captures_first_reports_last_and_private_files_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            guide = make_guide(Path(temporary))
            (guide / "captures" / "retirees").mkdir()
            (guide / "captures" / "retirees" / "000009.png").write_bytes(b"retiree")
            (guide / ".cache").write_text("x")
            (guide / "steps.json.tmp").write_text("x")
            names = [item.relative for item in sync.local_files(guide)]
            self.assertTrue(all(name.startswith("captures/") for name in names[:3]))
            self.assertEqual(names[-3:], ["steps.json", "rapport.md", "rapport.html"])
            self.assertFalse(any("retirees" in name or name.startswith(".") or name.endswith(".tmp")
                                 for name in names))


class ValidationTests(unittest.TestCase):
    def test_insecure_or_incomplete_configurations_are_refused(self):
        with self.assertRaisesRegex(SyncError, "HTTPS"):
            sync.validate(SyncConfig("webdav", url="http://cloud.exemple.fr/dav", user="a"))
        sync.validate(SyncConfig("webdav", url="http://127.0.0.1:8080/dav", user="a"))  # Machine locale.
        with self.assertRaisesRegex(SyncError, "utilisateur"):
            sync.validate(SyncConfig("webdav", url="https://cloud.exemple.fr/dav"))
        with self.assertRaises(SyncError):
            sync.validate(SyncConfig("folder"))
        with self.assertRaises(SyncError):
            sync.validate(SyncConfig("sftp", host="nas"))
        with self.assertRaises(SyncError):
            sync.validate(SyncConfig("sftp", host="nas", user="u", port=70000))

    def test_preferences_and_secret_keys(self):
        config = sync.config_from_preferences({"sync_kind": "sftp", "sync_host": "nas.local", "sync_port": "2222",
                                               "sync_user": "tech", "sync_remote": "/srv/guides"})
        self.assertEqual(config.port, 2222)
        self.assertEqual(config.label, "sftp://tech@nas.local:2222/srv/guides")
        self.assertEqual(config.secret_key, "sftp:tech@nas.local:2222")
        self.assertIsNone(sync.config_from_preferences({"sync_kind": ""}))

    def test_passwords_go_to_the_system_keyring(self):
        stored = {}

        class Keyring:
            @staticmethod
            def get_password(service, key):
                return stored.get((service, key))

            @staticmethod
            def set_password(service, key, value):
                stored[(service, key)] = value

            @staticmethod
            def delete_password(service, key):
                stored.pop((service, key), None)

        config = SyncConfig("webdav", url="https://cloud.exemple.fr/dav", user="tech")
        with patch.dict("sys.modules", {"keyring": Keyring}):
            self.assertTrue(sync.set_password(config, "secret"))
            self.assertEqual(sync.get_password(config), "secret")
            sync.delete_password(config)
            self.assertIsNone(sync.get_password(config))
        self.assertEqual(stored, {})

        class Broken:
            @staticmethod
            def get_password(service, key):
                raise RuntimeError("aucun coffre")

            set_password = get_password

        with patch.dict("sys.modules", {"keyring": Broken}):
            self.assertIsNone(sync.get_password(config))
            self.assertFalse(sync.set_password(config, "x"))


class FolderSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guide = make_guide(self.root / "local")
        self.nas = self.root / "nas"
        self.nas.mkdir()

    def test_full_then_incremental_sync_and_removed_steps(self):
        target = FolderTarget(self.nas)
        target.check()
        first = sync.sync_guide(self.guide, target)
        remote = self.nas / self.guide.name
        self.assertEqual(first.sent, len(sync.local_files(self.guide)))
        self.assertEqual((remote / "captures" / "000001.png").read_bytes(),
                         (self.guide / "captures" / "000001.png").read_bytes())
        self.assertFalse(list(remote.rglob(f"*{sync.TEMP_SUFFIX}")))
        second = sync.sync_guide(self.guide, target)
        self.assertEqual(second.unchanged, 3)  # Captures inchangées.
        self.assertEqual(second.sent, 3)  # Rapports toujours renvoyés.
        name, _, steps = read_session(self.guide)
        remove_step(self.guide, name, steps, 0, keep=True)
        third = sync.sync_guide(self.guide, target)
        self.assertEqual(third.removed, 1)
        self.assertEqual(sorted(path.name for path in (remote / "captures").glob("*.png")),
                         ["000002.png", "000003.png"])
        self.assertFalse((remote / "captures" / "retirees").exists())
        self.assertIn("retirée", third.summary())

    def test_cancellation_and_unwritable_folder(self):
        with self.assertRaisesRegex(SyncError, "interrompue"):
            sync.sync_guide(self.guide, FolderTarget(self.nas), cancelled=lambda: True)
        with self.assertRaisesRegex(SyncError, "inaccessible"):
            FolderTarget(self.root / "absent").check()
        with self.assertRaisesRegex(SyncError, "steps.json"):
            sync.sync_guide(self.nas, FolderTarget(self.nas))

    def test_background_job_reports_progress_and_result(self):
        events = []
        config = SyncConfig("folder", folder=str(self.nas))
        job = sync.SyncJob(self.guide, config, None, events.append).start()
        job.thread.join(10)
        kinds = [event["type"] for event in events]
        self.assertIn("sync_progress", kinds)
        self.assertEqual(kinds[-1], "sync_done")
        self.assertEqual(events[-1]["result"].destination, str(self.nas))
        failing = sync.SyncJob(self.guide, SyncConfig("folder", folder=""), None, events.append).start()
        failing.thread.join(10)
        self.assertEqual(events[-1]["type"], "sync_error")


class DavHandler(http.server.BaseHTTPRequestHandler):
    """Serveur WebDAV minimal (PROPFIND, MKCOL, PUT, MOVE, DELETE) pour les tests."""

    root: Path
    auth = "Basic " + base64.b64encode(b"tech:secret").decode()
    methods: list

    def log_message(self, *args):
        pass

    def _path(self, url=None) -> Path:
        path = urllib.parse.unquote(urllib.parse.urlsplit(url or self.path).path)
        assert path.startswith("/dav")
        return self.root.joinpath(*[part for part in path[4:].split("/") if part])

    def _guard(self) -> bool:
        self.methods.append(self.command)
        if self.headers.get("Authorization") != self.auth:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def _empty(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MKCOL(self):  # noqa: N802
        if not self._guard():
            return
        path = self._path()
        if path.exists():
            return self._empty(405)
        if not path.parent.exists():
            return self._empty(409)
        path.mkdir()
        self._empty(201)

    def do_PUT(self):  # noqa: N802
        if not self._guard():
            return
        path = self._path()
        length = int(self.headers["Content-Length"])
        path.write_bytes(self.rfile.read(length))
        self._empty(201)

    def do_MOVE(self):  # noqa: N802
        if not self._guard():
            return
        self._path().replace(self._path(self.headers["Destination"]))
        self._empty(201)

    def do_DELETE(self):  # noqa: N802
        if not self._guard():
            return
        path = self._path()
        if not path.exists():
            return self._empty(404)
        path.unlink()
        self._empty(204)

    def do_PROPFIND(self):  # noqa: N802
        if not self._guard():
            return
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path = self._path()
        if not path.exists():
            return self._empty(404)
        entries = [path] + (sorted(path.iterdir()) if path.is_dir() and self.headers.get("Depth") == "1" else [])
        base = urllib.parse.urlsplit(self.path).path.rstrip("/")
        parts = []
        for entry in entries:
            href = base if entry == path else f"{base}/{urllib.parse.quote(entry.name)}"
            kind = "<d:collection/>" if entry.is_dir() else ""
            size = "" if entry.is_dir() else f"<d:getcontentlength>{entry.stat().st_size}</d:getcontentlength>"
            parts.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>{kind}"
                         f"</d:resourcetype>{size}</d:prop></d:propstat></d:response>")
        body = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(parts) + "</d:multistatus>").encode()
        self.send_response(207)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WebDavSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guide = make_guide(self.root / "local")
        self.served = self.root / "serveur"
        self.served.mkdir()
        handler = type("Handler", (DavHandler,), {"root": self.served, "methods": []})
        self.handler = handler
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/dav/StepWatch guides"

    def config(self):
        return SyncConfig("webdav", url=self.url, user="tech")

    def test_sync_creates_folders_uploads_atomically_and_skips_unchanged(self):
        message = sync.test_connection(self.config(), "secret")
        self.assertIn("Connexion réussie", message)
        target = sync.open_target(self.config(), "secret")
        result = sync.sync_guide(self.guide, target)
        remote = self.served / "StepWatch guides" / self.guide.name
        self.assertEqual(result.sent, 6)
        self.assertEqual((remote / "rapport.html").read_bytes(), (self.guide / "rapport.html").read_bytes())
        self.assertFalse(list(remote.rglob(f"*{sync.TEMP_SUFFIX}")))
        self.assertIn("MOVE", self.handler.methods)
        again = sync.sync_guide(self.guide, sync.open_target(self.config(), "secret"))
        self.assertEqual((again.sent, again.unchanged), (3, 3))
        name, _, steps = read_session(self.guide)
        remove_step(self.guide, name, steps, 2, keep=True)
        third = sync.sync_guide(self.guide, sync.open_target(self.config(), "secret"))
        self.assertEqual(third.removed, 1)
        self.assertFalse((remote / "captures" / "000003.png").exists())

    def test_wrong_password_and_missing_password(self):
        with self.assertRaisesRegex(SyncError, "401"):
            sync.test_connection(self.config(), "faux")
        with self.assertRaisesRegex(SyncError, "manquant"):
            sync.open_target(self.config(), None)

    def test_unreachable_server(self):
        port = self.server.server_address[1]
        self.server.shutdown()
        self.server.server_close()
        config = SyncConfig("webdav", url=f"http://127.0.0.1:{port}/dav", user="tech")
        with self.assertRaisesRegex(SyncError, "injoignable"):
            sync.test_connection(config, "secret")

    def test_hostile_xml_is_refused(self):
        target = sync.WebDavTarget(self.url, "tech", "secret",
                                   opener=lambda request, timeout: _Response(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>'))
        with self.assertRaisesRegex(SyncError, "refusée"):
            target.size("x")


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self, _limit=None):
        return self.payload

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeSftp:
    """Client SFTP simulé, adossé à un dossier local."""

    def __init__(self, root: Path):
        self.root = root

    def _p(self, path):
        return self.root / path.lstrip("/")

    def stat(self, path):
        return self._p(path).stat()

    def mkdir(self, path):
        self._p(path).mkdir()

    def putfo(self, handle, path, file_size=0, confirm=True):
        self._p(path).write_bytes(handle.read())

    def posix_rename(self, source, target):
        self._p(source).replace(self._p(target))

    def listdir_attr(self, path):
        import types
        return [types.SimpleNamespace(filename=entry.name, st_mode=entry.stat().st_mode, st_size=entry.stat().st_size)
                for entry in self._p(path).iterdir()]

    def remove(self, path):
        self._p(path).unlink()

    def close(self):
        pass


class SftpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guide = make_guide(self.root / "local")
        self.server = self.root / "sftp"
        self.server.mkdir()
        self.known_hosts = self.root / "conf" / "known_hosts"
        import paramiko
        self.host_key = paramiko.RSAKey.generate(1024)

    def client_factory(self, accepted):
        test = self

        class Client:
            def load_system_host_keys(self):
                pass

            def load_host_keys(self, path):
                self.known = Path(path).read_text()

            def set_missing_host_key_policy(self, policy):
                self.policy = policy

            def connect(self, host, **kwargs):
                if not accepted():
                    self.policy.missing_host_key(self, host, test.host_key)

            def open_sftp(self):
                return FakeSftp(test.server)

            def close(self):
                pass

        return Client

    def test_unknown_host_key_must_be_accepted_then_sync_works(self):
        config = SyncConfig("sftp", host="nas.local", user="tech", remote="/srv/guides")
        accepted = lambda: self.known_hosts.exists()  # noqa: E731
        target = sync.SftpTarget(config, "secret", self.known_hosts, client_factory=self.client_factory(accepted))
        with self.assertRaises(UnknownHostKey) as caught:
            sync.sync_guide(self.guide, target)
        self.assertTrue(caught.exception.fingerprint.startswith("SHA256:"))
        self.assertEqual(caught.exception.fingerprint, sync.fingerprint(self.host_key))
        sync.trust_host_key(caught.exception, self.known_hosts)
        self.assertIn("nas.local", self.known_hosts.read_text())
        target = sync.SftpTarget(config, "secret", self.known_hosts, client_factory=self.client_factory(accepted))
        result = sync.sync_guide(self.guide, target)
        target.close()
        self.assertEqual(result.sent, 6)
        remote = self.server / "srv" / "guides" / self.guide.name
        self.assertTrue((remote / "steps.json").is_file())
        self.assertFalse(list(remote.rglob(f"*{sync.TEMP_SUFFIX}")))

    def test_non_standard_port_keeps_the_configured_host_name(self):
        # paramiko passe « [hôte]:port » à la politique hors port 22 (constaté sur un vrai serveur).
        test = self

        class Client(self.client_factory(lambda: False)):
            def connect(self, host, port=22, **kwargs):
                self.policy.missing_host_key(self, f"[{host}]:{port}", test.host_key)

        config = SyncConfig("sftp", host="nas.local", port=2222, user="tech")
        target = sync.SftpTarget(config, None, self.known_hosts, client_factory=Client)
        with self.assertRaises(UnknownHostKey) as caught:
            target.check()
        self.assertEqual((caught.exception.host, caught.exception.port), ("nas.local", 2222))
        sync.trust_host_key(caught.exception, self.known_hosts)
        self.assertTrue(self.known_hosts.read_text().startswith("[nas.local]:2222 "))

    def test_non_standard_port_is_recorded_with_brackets(self):
        error = UnknownHostKey("nas.local", 2222, "ssh-rsa", sync.fingerprint(self.host_key), self.host_key)
        sync.trust_host_key(error, self.known_hosts)
        self.assertIn("[nas.local]:2222", self.known_hosts.read_text())


class _Cleanup(unittest.TestCase):
    def test_temp_dirs_are_removed(self):
        directory = Path(tempfile.mkdtemp())
        shutil.rmtree(directory)
        self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
