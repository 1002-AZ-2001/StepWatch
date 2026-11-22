"""Mises à jour : version publiée, vérification SHA-256, installation, sans réseau."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from stepwatch import updater
from stepwatch.instance import InstanceLock
from stepwatch.updater import Asset, Installation, Release, UpdateError

PAYLOAD = b"nouvelle version de StepWatch" * 1000
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def release_json(tag="v0.11.0", digest=f"sha256:{DIGEST}", **extra):
    data = {
        "tag_name": tag, "html_url": f"https://github.com/1002-AZ-2001/StepWatch/releases/tag/{tag}",
        "body": "## Nouveautés\n- Mise à jour automatique", "draft": False, "prerelease": False,
        "assets": [
            {"name": "StepWatch-0.11.0-windows-x64.exe", "size": len(PAYLOAD), "digest": digest,
             "browser_download_url": "https://github.com/x/StepWatch-0.11.0-windows-x64.exe"},
            {"name": "stepwatch_0.11.0_amd64.deb", "size": len(PAYLOAD), "digest": digest,
             "browser_download_url": "https://github.com/x/stepwatch_0.11.0_amd64.deb"},
            {"name": "SHA256SUMS-windows.txt", "size": 100, "digest": None,
             "browser_download_url": "https://github.com/x/SHA256SUMS-windows.txt"},
            {"name": "broken"},
        ],
    }
    data.update(extra)
    return data


class FakeOpener:
    """Réponses par URL ; enregistre les requêtes (en-têtes compris)."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        body = self.responses[request.full_url]
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(body)


class VersionTests(unittest.TestCase):
    def test_versions_are_compared_numerically(self):
        self.assertEqual(updater.parse_version("v0.10.0"), (0, 10, 0))
        self.assertIsNone(updater.parse_version("v1.0.0-beta"))
        self.assertTrue(updater.is_newer("0.10.0", "0.9.0"))
        self.assertFalse(updater.is_newer("0.9.0", "0.9.0"))
        self.assertFalse(updater.is_newer("0.8.5", "0.9.0"))
        self.assertFalse(updater.is_newer("nimporte", "0.9.0"))

    def test_latest_release_is_parsed_with_a_generic_user_agent(self):
        opener = FakeOpener({updater.LATEST_URL: json.dumps(release_json()).encode()})
        release = updater.fetch_latest(opener=opener)
        self.assertEqual(release.version, "0.11.0")
        self.assertEqual(len(release.assets), 3)  # L'entrée incomplète est ignorée.
        self.assertIn("Mise à jour automatique", release.notes)
        agent = opener.requests[0].get_header("User-agent")
        self.assertTrue(agent.startswith("StepWatch/"))
        self.assertNotIn("@", agent)

    def test_prereleases_network_errors_and_garbage_are_reported(self):
        for body in (json.dumps(release_json(prerelease=True)).encode(), b"[]", b"{pas du json"):
            with self.assertRaises(UpdateError):
                updater.fetch_latest(opener=FakeOpener({updater.LATEST_URL: body}))
        with self.assertRaisesRegex(UpdateError, "joindre GitHub"):
            updater.fetch_latest(opener=FakeOpener({updater.LATEST_URL: OSError("hors ligne")}))

    def test_asset_matches_the_installation(self):
        release = updater.parse_release(release_json())
        self.assertEqual(updater.asset_for(release, Installation("exe")).name, "StepWatch-0.11.0-windows-x64.exe")
        self.assertEqual(updater.asset_for(release, Installation("deb")).name, "stepwatch_0.11.0_amd64.deb")
        self.assertIsNone(updater.asset_for(release, Installation("arch")))
        self.assertIsNone(updater.asset_for(release, Installation("source")))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.release = updater.parse_release(release_json())
        self.asset = self.release.asset("StepWatch-0.11.0-windows-x64.exe")

    def opener(self, payload=PAYLOAD, sums=None):
        sums = f"{DIGEST}  StepWatch-0.11.0-windows-x64.exe\n" if sums is None else sums
        return FakeOpener({self.asset.url: payload, "https://github.com/x/SHA256SUMS-windows.txt": sums.encode()})

    def test_verified_download_reports_progress(self):
        progress = []
        path = updater.download(self.release, self.asset, self.directory, opener=self.opener(),
                                progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))

    def test_altered_file_is_rejected_and_deleted(self):
        altered = PAYLOAD[:-1] + b"!"
        with self.assertRaisesRegex(UpdateError, "SHA-256"):
            updater.download(self.release, self.asset, self.directory, opener=self.opener(payload=altered))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_disagreeing_or_missing_checksums_are_refused(self):
        with self.assertRaisesRegex(UpdateError, "concordent pas"):
            updater.download(self.release, self.asset, self.directory,
                             opener=self.opener(sums=f"{'0' * 64}  StepWatch-0.11.0-windows-x64.exe\n"))
        with self.assertRaisesRegex(UpdateError, "absent"):
            updater.download(self.release, self.asset, self.directory, opener=self.opener(sums="rien\n"))
        bare = Release("0.11.0", "v0.11.0", "", "", (Asset("stepwatch_0.11.0_amd64.deb", "https://x/a.deb", 1),))
        with self.assertRaisesRegex(UpdateError, "Aucune somme"):
            updater.download(bare, bare.assets[0], self.directory, opener=FakeOpener({}))

    def test_truncated_cancelled_or_plain_http_downloads_fail(self):
        with self.assertRaisesRegex(UpdateError, "incomplet"):
            updater.download(self.release, self.asset, self.directory, opener=self.opener(payload=PAYLOAD[:10]))
        with self.assertRaisesRegex(UpdateError, "annulé"):
            updater.download(self.release, self.asset, self.directory, opener=self.opener(), cancelled=lambda: True)
        insecure = Asset("stepwatch_0.11.0_amd64.deb", "http://x/a.deb", 1, f"sha256:{DIGEST}")
        with self.assertRaisesRegex(UpdateError, "HTTPS"):
            updater.download(self.release, insecure, self.directory, opener=FakeOpener({}))
        self.assertEqual(list(self.directory.iterdir()), [])


class InstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_detection(self):
        self.assertEqual(updater.detect_installation(frozen=False).kind, "source")
        folder = self.directory / "StepWatch"
        exe = folder / "StepWatch.exe"
        portable = updater.detect_installation(frozen=True, platform="win32", executable=str(exe),
                                               bundle_dir=str(folder / "_internal"))
        self.assertEqual((portable.kind, portable.path), ("portable", folder))
        single = updater.detect_installation(frozen=True, platform="win32", executable=str(exe),
                                             bundle_dir=str(self.directory / "_MEI1234"))
        self.assertEqual((single.kind, single.path), ("exe", exe))
        root = self.directory / "racine"
        (root / "var/lib/dpkg/info").mkdir(parents=True)
        linux = {"frozen": True, "platform": "linux", "executable": "/opt/stepwatch/StepWatch", "root": root}
        self.assertEqual(updater.detect_installation(**linux).kind, "source")
        (root / "var/lib/pacman/local/stepwatch-0.9.0-1").mkdir(parents=True)
        self.assertEqual(updater.detect_installation(**linux).kind, "arch")
        (root / "var/lib/dpkg/info/stepwatch.list").touch()
        self.assertEqual(updater.detect_installation(**linux).kind, "deb")
        elsewhere = dict(linux, executable=str(self.directory / "StepWatch"))
        self.assertEqual(updater.detect_installation(**elsewhere).kind, "source")

    def test_executable_is_swapped_and_old_copy_cleaned(self):
        exe = self.directory / "StepWatch-0.9.0-windows-x64.exe"
        exe.write_bytes(b"ancienne")
        new = self.directory / "telecharge.exe"
        new.write_bytes(b"nouvelle")
        command = updater.apply(Installation("exe", exe), new)
        self.assertEqual(command, [str(exe), "--after-update"])
        self.assertEqual(exe.read_bytes(), b"nouvelle")
        old = exe.with_name(exe.name + ".old")
        self.assertEqual(old.read_bytes(), b"ancienne")
        updater.cleanup_previous(exe)
        self.assertFalse(old.exists())

    def make_zip(self, names):
        archive = self.directory / "portable.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for name in names:
                bundle.writestr(name, "x")
        return archive

    def test_portable_archive_is_checked_then_handed_to_the_helper(self):
        target = self.directory / "installe"
        target.mkdir()
        archive = self.make_zip(["StepWatch/StepWatch.exe", "StepWatch/_internal/base.dll"])
        popen = Mock()
        self.assertIsNone(updater.apply(Installation("portable", target), archive, popen=popen, pid=4321))
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertEqual(command[command.index("-WaitFor") + 1], "4321")
        source = Path(command[command.index("-Source") + 1])
        self.assertTrue((source / "_internal" / "base.dll").exists())
        self.assertEqual(Path(command[command.index("-Target") + 1]), target)
        self.assertEqual(popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertTrue((source.parent / "appliquer-mise-a-jour.ps1").read_text(encoding="utf-8-sig")
                        .startswith("param("))

    def test_unexpected_or_escaping_archives_are_refused(self):
        for names in (["autre/StepWatch.exe"], ["StepWatch/StepWatch.exe", "StepWatch/../../evil.dll"],
                      ["StepWatch/StepWatch.exe", "ailleurs.txt"]):
            with self.subTest(names=names), self.assertRaises(UpdateError):
                updater.extract_portable(self.make_zip(names), self.directory / "extrait")

    def test_packages_use_pkexec_and_report_cancellation(self):
        package = self.directory / "stepwatch_0.11.0_amd64.deb"
        package.write_bytes(b"deb")
        run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.assertEqual(updater.apply(Installation("deb"), package, run=run), ["/usr/bin/stepwatch", "--after-update"])
        self.assertEqual(run.call_args.args[0], ["pkexec", "apt-get", "install", "-y", str(package)])
        self.assertEqual(updater.package_command(Installation("arch"), package)[:3], ["pkexec", "pacman", "-U"])
        run.return_value = subprocess.CompletedProcess([], 126, "", "")
        with self.assertRaisesRegex(UpdateError, "annulée"):
            updater.apply(Installation("deb"), package, run=run)
        run.return_value = subprocess.CompletedProcess([], 100, "", "E: dépendances\n")
        with self.assertRaisesRegex(UpdateError, "dépendances"):
            updater.apply(Installation("deb"), package, run=run)
        with self.assertRaises(UpdateError):
            updater.apply(Installation("source"), package)

    def test_relaunch_starts_an_independent_process(self):
        popen = Mock()
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/opt/stepwatch/_internal", "LD_LIBRARY_PATH_ORIG": "/usr/x"}):
            updater.relaunch(["/usr/bin/stepwatch", "--after-update"], popen=popen)
        options = popen.call_args.kwargs
        self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(options["env"]["LD_LIBRARY_PATH"], "/usr/x")
        if sys.platform != "win32":
            self.assertTrue(options["start_new_session"])

    def test_instance_lock_waits_for_the_previous_version(self):
        if sys.platform == "win32":
            self.skipTest("Verrou POSIX.")
        path = self.directory / "stepwatch.lock"
        first = InstanceLock(path)
        self.assertTrue(first.acquire())
        second = InstanceLock(path)
        self.assertFalse(second.acquire(wait=0.3))
        first.release()
        self.assertTrue(second.acquire(wait=1))
        second.release()


class NotesTests(unittest.TestCase):
    def test_release_notes_are_shown_without_markdown_syntax(self):
        from stepwatch.update_dialog import format_notes
        lines = format_notes("## Nouveautés\n\n- **Mise à jour** `auto`\n* [lien](https://x)\n\n\n"
                             "| Fichier | Usage |\n| --- | --- |\n| a.exe | Windows |")
        self.assertEqual(lines, [("Nouveautés", "heading"), ("", "text"), ("• Mise à jour auto", "text"),
                                 ("• lien", "text"), ("", "text"), ("Fichier — Usage", "text"),
                                 ("a.exe — Windows", "text")])


@unittest.skipUnless(sys.platform == "win32", "Script PowerShell de la version portable.")
class PortableHelperTests(unittest.TestCase):
    def test_helper_copies_files_after_the_process_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "StepWatch"
            target.mkdir()
            (target / "garde.txt").write_text("utilisateur")
            (target / "version.txt").write_text("0.9.0")
            source = root / "telechargement" / "extrait" / "StepWatch"
            source.mkdir(parents=True)
            (source / "version.txt").write_text("0.11.0")
            finished = subprocess.Popen([sys.executable, "-c", "pass"])
            finished.wait()
            helper = updater.start_portable_helper(source, target, wait_for=finished.pid, restart=False)
            self.assertEqual(helper.wait(timeout=120), 0)
            self.assertEqual((target / "version.txt").read_text(), "0.11.0")
            self.assertEqual((target / "garde.txt").read_text(), "utilisateur")
            self.assertFalse((root / "telechargement").exists())


if __name__ == "__main__":
    unittest.main()
