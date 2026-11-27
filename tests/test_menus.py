"""Barre de menus, fenêtre Paramètres, thèmes, zone de notification et mises à jour."""

import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import qt_helpers  # noqa: F401 - plateforme Qt hors écran, avant toute fenêtre
from qt_helpers import close_app, log_text, make_app, pump

from PIL import Image
from PySide6.QtGui import QKeySequence

from stepwatch import theme
from stepwatch.settings import Preferences
from stepwatch.storage import STANDALONE_REPORT, SessionStore, read_session


class FakeRecorder:
    state = "recording"

    def __init__(self):
        self.events = queue.Queue()
        self.store = Mock(directory=None)  # Comme Recorder.store : un stockage de session.

    def set_excluded_regions(self, regions, masked=()):
        pass

    def pause(self):
        self.state = "paused"

    def resume(self):
        self.state = "recording"

    def stop(self, timeout=5):
        self.state = "stopped"
        return True


class MenuAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prefs = Preferences(Path(self.temp.name) / "conf" / "settings.json")
        self.app = make_app(preferences=self.prefs)
        self.app.output.set(self.temp.name)
        store = SessionStore(Path(self.temp.name), "Guide menus")
        for colour in ("red", "green"):
            store.save(Image.new("RGB", (32, 24), colour), "local_click", click=(2, 2))
        store.close()
        self.directory = store.directory

    def tearDown(self):
        app = self.app
        try:
            app._recorder = None
            close_app(app)
        finally:
            theme.use_palette(False)
            self.app = None
            self.temp.cleanup()

    def pump(self, condition=lambda: True, timeout=5):
        pump(condition, timeout, app=self.app)

    def enabled(self, menu, label):
        return self.app.action(menu, label).isEnabled()

    def test_menu_bar_lists_every_area(self):
        labels = [action.text() for action in self.app.menubar.actions()]
        self.assertEqual(labels, ["Fichier", "Enregistrement", "Guide", "Affichage", "Aide"])
        self.assertEqual(self.app.action("Fichier", "Paramètres…").shortcut(), QKeySequence("Ctrl+,"))
        self.assertEqual(self.app.action("Fichier", "Ouvrir un guide…").shortcut(), QKeySequence("Ctrl+O"))
        # Raccourcis de l'éditeur : affichés, mais gérés par l'onglet Guide.
        self.assertIn("\tSuppr", self.app.action("Guide", "Supprimer l’étape").text())

    def test_entry_states_follow_the_recording(self):
        self.app._refresh_menus()
        self.assertTrue(self.enabled("Enregistrement", "Démarrer l’enregistrement"))
        self.assertFalse(self.enabled("Enregistrement", "Arrêter"))
        self.assertFalse(self.enabled("Guide", "Supprimer l’étape"))
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump()
        self.app._refresh_menus()
        self.assertFalse(self.enabled("Enregistrement", "Démarrer l’enregistrement"))
        self.assertTrue(self.enabled("Enregistrement", "Arrêter"))
        self.app.action("Enregistrement", "Pause / Reprendre").trigger()
        self.assertEqual(recorder.state, "paused")
        self.app.action("Enregistrement", "Arrêter").trigger()
        self.assertEqual(recorder.state, "stopped")

    def test_view_menu_switches_tabs_and_shows_advanced_settings(self):
        self.app.action("Affichage", "Onglet Journal").trigger()
        self.assertEqual(self.app._tabs.currentIndex(), 2)
        self.app.action("Affichage", "Onglet Enregistrer").trigger()
        self.assertEqual(self.app._tabs.currentIndex(), 0)
        self.app.action("Affichage", "Réglages avancés").trigger()
        self.assertTrue(self.app._advanced_open.get())
        self.assertFalse(self.app._advanced.isHidden())
        self.app.advanced_button.click()
        self.assertFalse(self.app.action("Affichage", "Réglages avancés").isChecked())
        self.assertTrue(self.app._advanced.isHidden())

    def test_menu_commands_are_ignored_while_closing(self):
        command = Mock()
        self.app._closing = True
        self.app._menu_command(command)
        command.assert_not_called()
        self.app._closing = False
        self.app._menu_command(command)
        command.assert_called_once()

    def test_settings_window_saves_and_applies_preferences(self):
        self.app._open_settings()
        dialog = self.app._settings_dialog
        dialog.vars["name_template"].set("Poste {date}")
        self.assertIn("Exemple", dialog.preview.text())
        dialog.vars["bar_position"].set("En haut de l’écran")
        dialog.vars["keep_retired"].set(False)
        dialog.vars["sound"].set(True)
        dialog.size_combo.setCurrentText("115 %")
        dialog.theme_combo.setCurrentText("Sombre")
        dialog._apply()
        values = Preferences(self.prefs.path).values  # Relu sur le disque.
        self.assertEqual(values["name_template"], "Poste {date}")
        self.assertEqual(values["bar_position"], "haut")
        self.assertFalse(values["keep_retired"])
        self.assertTrue(values["sound"])
        self.assertEqual(values["font_scale"], 115)
        self.assertEqual(values["theme"], "sombre")
        self.assertRegex(self.app.name.get(), r"^Poste \d{2}/\d{2}/\d{4}$")
        self.assertEqual(theme.PALETTE["bg"], theme.DARK["bg"])
        self.assertIn("11.5pt", self.app.qapp.styleSheet())
        self.assertIn("Paramètres enregistrés", log_text(self.app))
        self.app._reset_settings()
        self.assertEqual(self.prefs.values["bar_position"], "bas")
        self.assertEqual(self.app.name.get(), "Nouvelle intervention")

    def test_theme_menu_and_system_theme(self):
        self.app._choose_theme("sombre")
        self.assertEqual(theme.PALETTE["surface"], theme.DARK["surface"])
        self.assertEqual(Preferences(self.prefs.path).values["theme"], "sombre")
        self.app._choose_theme("clair")
        self.assertEqual(theme.PALETTE["surface"], theme.LIGHT["surface"])
        with patch.object(self.app, "_system_dark", return_value=True):
            self.app._choose_theme("systeme")
        self.assertEqual(theme.PALETTE["surface"], theme.DARK["surface"])
        self.assertTrue(theme.is_dark("systeme", True))
        self.assertFalse(theme.is_dark("systeme", False))

    def test_deleting_without_keeping_the_capture_cannot_be_undone(self):
        self.prefs.values.update(keep_retired=False, confirm_delete=False)
        review = self.app._review
        review.load(self.directory)
        self.pump(lambda: review.directory == self.directory and not review.busy)
        review.select(0)
        self.pump(lambda: review._selected == "0" and not review.busy)
        image = review.steps[0]["image"]
        with patch("stepwatch.review.messagebox.askyesno", return_value=True) as asked:
            review.delete_button.click()
        asked.assert_called_once()  # Toujours confirmé quand la capture est effacée.
        self.assertFalse((self.directory / image).exists())
        self.assertFalse((self.directory / "captures" / "retirees").exists())
        self.assertEqual(review._undo, [])
        self.prefs.values.update(keep_retired=True, confirm_delete=False)
        review.select(0)
        self.pump(lambda: review._selected == "0" and not review.busy)
        with patch("stepwatch.review.messagebox.askyesno") as asked:
            review.delete_button.click()
        asked.assert_not_called()
        self.assertEqual(len(read_session(self.directory)[2]), 0)

    def test_after_stop_guide_tab_new_name_and_automatic_standalone_report(self):
        self.prefs.values.update(open_guide_after_stop=True, auto_standalone=True, name_template="Suite {heure}")
        self.app._use_session(self.directory)
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump()
        recorder.stop()
        self.pump(lambda: (self.directory / STANDALONE_REPORT).exists()
                  and not self.app._background_running(), timeout=10)
        self.assertIs(self.app._tabs.currentWidget(), self.app._review)
        self.assertRegex(self.app.name.get(), r"^Suite \d{2}:\d{2}$")

    def test_check_installation_reports_success(self):
        with patch("stepwatch.ui.messagebox.showinfo") as shown:
            self.app.action("Aide", "Vérifier l’installation").trigger()
            self.pump(lambda: shown.called, timeout=15)
        self.assertIn("Installation vérifiée", shown.call_args[0][1])

    def test_about_mentions_qt_and_its_licence(self):
        with patch("stepwatch.ui.messagebox.showinfo") as shown:
            self.app.action("Aide", "À propos de StepWatch").trigger()
        self.assertIn("Qt", shown.call_args.args[1])
        self.assertIn("LGPL", shown.call_args.args[1])

    def test_tray_icon_follows_the_setting(self):
        with patch("stepwatch.ui.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
            self.prefs.values["tray_icon"] = True
            self.app._sync_tray()
            self.assertIsNotNone(self.app._tray)
            recorder = FakeRecorder()
            self.app._recorder = recorder
            self.app._compact_session = False
            self.app._controls_key = None
            self.pump()
            self.assertTrue(self.app._tray_actions["stop"].isEnabled())
            self.assertFalse(self.app._tray_actions["start"].isEnabled())
            self.app._tray_actions["pause"].trigger()
            self.assertEqual(recorder.state, "paused")
            self.assertIn("En pause", self.app._tray.toolTip())
            self.prefs.values["tray_icon"] = False
            self.app._sync_tray()
            self.assertIsNone(self.app._tray)

    # ----- Mises à jour -----------------------------------------------------------

    def release(self, version="99.0.0"):
        from stepwatch.updater import Asset, Release
        return Release(version, f"v{version}", "## Nouveautés\n- Essai", "https://github.com/x/releases/tag/v99.0.0",
                       (Asset("stepwatch_99.0.0_amd64.deb", "https://github.com/x/a.deb", 3, "sha256:" + "0" * 64),))

    def test_startup_check_is_daily_optional_and_needs_saved_preferences(self):
        with patch.object(self.app, "check_updates") as check:
            self.app.schedule_update_check(delay_ms=0)
            self.pump(lambda: check.called)
            check.assert_called_once_with(manual=False)
            check.reset_mock()
            self.prefs.values["last_update_check"] = time.strftime("%Y-%m-%d")
            self.app.schedule_update_check(delay_ms=0)
            self.prefs.values.update(last_update_check="", check_updates=False)
            self.app.schedule_update_check(delay_ms=0)
            self.app._prefs = Preferences.memory()
            self.app.schedule_update_check(delay_ms=0)
            for _ in range(5):
                self.pump()
                time.sleep(.01)
            check.assert_not_called()
        self.assertTrue(self.app.action("Aide", "Rechercher des mises à jour…").isEnabled())

    def test_new_version_is_offered_then_can_be_skipped(self):
        from stepwatch.updater import Installation
        with patch("stepwatch.ui.updater.fetch_latest", return_value=self.release()), \
                patch("stepwatch.ui.updater.detect_installation", return_value=Installation("deb")):
            self.app.check_updates(manual=False)
            self.pump(lambda: self.app._update_dialog is not None)
        dialog = self.app._update_dialog
        self.assertIn("Essai", dialog.notes.toPlainText())
        self.assertEqual(dialog.install_button.text(), "Installer et redémarrer")
        saved = Preferences(self.prefs.path).values
        self.assertEqual(saved["last_update_check"], time.strftime("%Y-%m-%d"))
        dialog.skip_button.click()
        self.assertEqual(Preferences(self.prefs.path).values["skipped_version"], "99.0.0")
        self.app._update_dialog = None
        with patch("stepwatch.ui.updater.fetch_latest", return_value=self.release()):
            self.app.check_updates(manual=False)
            self.pump(lambda: not self.app._update_thread.is_alive() and self.app._background_events.empty())
        self.assertIsNone(self.app._update_dialog)  # Ignorée au démarrage…
        with patch("stepwatch.ui.updater.fetch_latest", return_value=self.release()), \
                patch("stepwatch.ui.updater.detect_installation", return_value=Installation("source")):
            self.app.check_updates(manual=True)
            self.pump(lambda: self.app._update_dialog is not None)  # … mais pas sur demande.
        self.assertEqual(self.app._update_dialog.install_button.text(), "Ouvrir la page de téléchargement")

    def test_manual_check_reports_up_to_date_and_errors(self):
        from stepwatch import __version__
        from stepwatch.updater import UpdateError
        with patch("stepwatch.ui.updater.fetch_latest", return_value=self.release(__version__)), \
                patch("stepwatch.ui.messagebox.showinfo") as shown:
            self.app.check_updates(manual=True)
            self.pump(lambda: shown.called)
        self.assertIn("dernière version", shown.call_args.args[1])
        with patch("stepwatch.ui.updater.fetch_latest", side_effect=UpdateError("Impossible de joindre GitHub")), \
                patch("stepwatch.ui.messagebox.showerror") as error:
            self.app.check_updates(manual=True)
            self.pump(lambda: error.called)
        self.assertIsNone(self.app._update_dialog)

    def test_release_found_while_recording_waits_for_the_stop(self):
        from stepwatch.updater import Installation
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._last_state = "recording"
        self.app._compact_session = False
        with patch("stepwatch.ui.updater.fetch_latest", return_value=self.release()), \
                patch("stepwatch.ui.updater.detect_installation", return_value=Installation("deb")):
            self.app.check_updates(manual=False)
            self.pump(lambda: self.app._pending_release is not None)
            self.assertIsNone(self.app._update_dialog)
            self.assertIn("Arrêtez", self.app._update_blocked())
            recorder.stop()
            self.pump(lambda: self.app._update_dialog is not None)

    def test_install_downloads_applies_and_restarts(self):
        from stepwatch.updater import Installation
        downloaded = Path(self.temp.name) / "stepwatch_99.0.0_amd64.deb"
        with patch("stepwatch.ui.updater.detect_installation", return_value=Installation("deb")):
            dialog = self.app._show_update(self.release())
        with patch.object(self.app, "_close") as close, \
                patch("stepwatch.updater.download", return_value=downloaded) as download, \
                patch("stepwatch.updater.apply", return_value=["/usr/bin/stepwatch", "--after-update"]) as apply, \
                patch("stepwatch.ui.updater.relaunch") as relaunch:
            dialog.install_button.click()
            self.pump(lambda: close.called)
        self.assertEqual(download.call_args.args[1].name, "stepwatch_99.0.0_amd64.deb")
        apply.assert_called_once_with(Installation("deb"), downloaded)
        relaunch.assert_called_once_with(["/usr/bin/stepwatch", "--after-update"])

    def test_failed_install_keeps_the_dialog_usable(self):
        from stepwatch.updater import Installation, UpdateError
        with patch("stepwatch.ui.updater.detect_installation", return_value=Installation("deb")):
            dialog = self.app._show_update(self.release())
        with patch("stepwatch.updater.download", side_effect=UpdateError("Somme SHA-256 différente")), \
                patch("stepwatch.update_dialog.messagebox.showerror") as error, \
                patch("stepwatch.ui.updater.relaunch") as relaunch:
            dialog.install_button.click()
            self.pump(lambda: error.called)
        self.assertIn("SHA-256", dialog.status())
        self.assertTrue(dialog.install_button.isEnabled())
        relaunch.assert_not_called()

    def test_settings_window_offers_the_update_check(self):
        self.app._open_settings()
        dialog = self.app._settings_dialog
        self.assertTrue(dialog.vars["check_updates"].get())
        dialog.vars["check_updates"].set(False)
        dialog._apply()
        self.assertFalse(Preferences(self.prefs.path).values["check_updates"])

    # ----- Synchronisation ----------------------------------------------------------

    def test_settings_tab_configures_a_network_folder_and_stores_the_password(self):
        nas = Path(self.temp.name) / "nas"
        nas.mkdir()
        self.app._open_settings()
        dialog = self.app._settings_dialog
        dialog.sync_combo.setCurrentText("Dossier réseau (NAS)")
        self.assertEqual(dialog.sync_pages.currentIndex(), 2)
        dialog.vars["sync_folder"].set(str(nas))
        dialog.test_connection()
        self.pump(lambda: "Connexion réussie" in dialog.test_status.text())
        dialog._apply()
        values = Preferences(self.prefs.path).values
        self.assertEqual((values["sync_kind"], values["sync_folder"]), ("folder", str(nas)))
        self.assertNotIn("sync_password", values)
        self.app._open_settings()
        dialog = self.app._settings_dialog
        dialog.sync_combo.setCurrentText("Nextcloud ou WebDAV")
        dialog.vars["sync_url"].set("https://cloud.exemple.fr/remote.php/dav/files/tech/StepWatch")
        dialog.vars["sync_user"].set("tech")
        dialog.webdav_password.setText("mot-de-passe-application")
        with patch("stepwatch.ui.sync.set_password", return_value=True) as stored:
            dialog._apply()
        self.assertEqual(stored.call_args.args[1], "mot-de-passe-application")
        self.assertNotIn("mot-de-passe-application", self.prefs.path.read_text(encoding="utf-8"))

    def test_menu_sync_sends_the_open_guide_to_a_network_folder(self):
        nas = Path(self.temp.name) / "nas"
        nas.mkdir()
        self.prefs.values.update(sync_kind="folder", sync_folder=str(nas))
        self.app._use_session(self.directory)
        self.app._refresh_menus()
        self.assertTrue(self.enabled("Fichier", "Synchroniser le guide"))
        self.app.action("Fichier", "Synchroniser le guide").trigger()
        self.pump(lambda: "Synchronisation terminée" in log_text(self.app), timeout=10)
        self.assertTrue((nas / self.directory.name / "rapport.html").is_file())
        self.assertFalse(self.app._sync_running())

    def test_sync_without_destination_explains_where_to_configure_it(self):
        self.app._use_session(self.directory)
        with patch("stepwatch.ui.messagebox.showinfo") as shown:
            self.app.sync_guide()
        self.assertIn("Paramètres › Synchronisation", shown.call_args.args[1])

    def test_automatic_sync_after_the_recording_stops(self):
        nas = Path(self.temp.name) / "nas"
        nas.mkdir()
        self.prefs.values.update(sync_kind="folder", sync_folder=str(nas), sync_auto=True,
                                 open_guide_after_stop=False)
        self.app._use_session(self.directory)
        recorder = FakeRecorder()
        self.app._recorder = recorder
        self.app._compact_session = False
        self.pump()
        recorder.stop()
        self.pump(lambda: (nas / self.directory.name / "steps.json").exists() and not self.app._sync_running(),
                  timeout=15)

    def test_webdav_password_is_asked_once_then_kept(self):
        self.prefs.values.update(sync_kind="webdav", sync_url="https://cloud.exemple.fr/dav", sync_user="tech")
        self.app._use_session(self.directory)
        started = []
        with patch("stepwatch.ui.sync.get_password", return_value=None), \
                patch("stepwatch.ui.sync.set_password", return_value=True) as stored, \
                patch("stepwatch.ui.simpledialog.askstring", return_value="secret") as asked, \
                patch("stepwatch.ui.sync.SyncJob") as job:
            job.return_value.start.side_effect = lambda: started.append(True) or job.return_value
            self.app.sync_guide()
        self.assertTrue(asked.call_args.kwargs["password"])
        stored.assert_called_once()
        self.assertEqual(job.call_args.args[2], "secret")
        self.assertTrue(started)

    def test_unknown_sftp_server_is_confirmed_before_trusting_it(self):
        from stepwatch.sync import UnknownHostKey
        error = UnknownHostKey("nas.local", 22, "ssh-ed25519", "SHA256:abc", key=None)
        with patch("stepwatch.ui.messagebox.askyesno", return_value=False) as asked, \
                patch("stepwatch.ui.sync.trust_host_key") as trust:
            self.app._sync_event({"type": "sync_host_key", "directory": str(self.directory), "error": error})
        self.assertIn("SHA256:abc", asked.call_args.args[1])
        trust.assert_not_called()
        with patch("stepwatch.ui.messagebox.askyesno", return_value=True), \
                patch("stepwatch.ui.sync.trust_host_key") as trust, \
                patch.object(self.app, "sync_guide") as again:
            self.app._sync_event({"type": "sync_host_key", "directory": str(self.directory), "error": error})
            self.pump(lambda: again.called)
        trust.assert_called_once()

    def test_sync_errors_are_shown_and_logged(self):
        with patch("stepwatch.ui.messagebox.showerror") as error:
            self.app._sync_event({"type": "sync_error", "directory": str(self.directory),
                                  "message": "Serveur WebDAV injoignable"})
        self.assertIn("reste complet", error.call_args.args[1])
        self.assertIn("injoignable", log_text(self.app))


if __name__ == "__main__":
    unittest.main()
