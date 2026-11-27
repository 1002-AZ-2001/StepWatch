"""Fenêtre « Paramètres » : préférences de StepWatch regroupées par thème."""

from __future__ import annotations

import queue
import sys
import threading
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from . import sync
from .qtcompat import Value, filedialog, messagebox
from .settings import CHOICES, expand_name
from .theme import set_role

BAR_POSITIONS = {"bas": "En bas de l’écran", "haut": "En haut de l’écran"}
THEME_LABELS = {"systeme": "Comme le système", "clair": "Clair", "sombre": "Sombre"}
SYNC_LABELS = {"": "Désactivée", **sync.KINDS}


class SettingsDialog(QDialog):
    """Modifie une copie des préférences ; ``on_apply`` reçoit les nouvelles valeurs."""

    def __init__(self, parent: QWidget, values: dict[str, Any], *,
                 on_apply: Callable[[dict[str, Any]], None], on_reset: Callable[[], None]):
        super().__init__(parent)
        self.setWindowTitle("Paramètres de StepWatch")
        self.setModal(True)
        self._on_apply, self._on_reset = on_apply, on_reset
        self.vars: dict[str, Value] = {}
        for key, value in values.items():
            if isinstance(value, (bool, str, int)):
                self.vars[key] = Value(value if isinstance(value, bool) else str(value))
        self.vars["bar_position"].set(BAR_POSITIONS.get(values["bar_position"], BAR_POSITIONS["bas"]))
        self.vars["font_scale"].set(f"{values['font_scale']} %")
        self.vars["theme"].set(THEME_LABELS.get(values["theme"], THEME_LABELS["systeme"]))
        self.vars["sync_kind"].set(SYNC_LABELS.get(values["sync_kind"], SYNC_LABELS[""]))
        self._initial_scale = values["font_scale"]
        self._test_events: queue.Queue = queue.Queue()
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(100)
        self._test_timer.timeout.connect(self._poll_test)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)  # Tous les onglets visibles : la fenêtre s'élargit.
        layout.addWidget(self.tabs)

        general = self._page("Général")
        general.addWidget(QLabel("Nom proposé pour un nouveau guide"))
        self.name_entry = self.vars["name_template"].bind_line(QLineEdit())
        self.name_entry.setMinimumWidth(420)
        general.addWidget(self.name_entry)
        self.preview = set_role(QLabel(""), "muted")
        general.addWidget(self.preview)
        self.vars["name_template"].trace(lambda _value: self._update_preview())
        self._update_preview()
        general.addSpacing(8)
        general.addWidget(QLabel("Dossier des guides"))
        folder = QHBoxLayout()
        folder.addWidget(self.vars["output"].bind_line(QLineEdit()), 1)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._choose_folder)
        folder.addWidget(browse)
        general.addLayout(folder)
        general.addSpacing(8)
        self._check(general, "check_updates", "Rechercher les mises à jour au démarrage (une fois par jour)")
        self._hint(general, "Seul accès réseau de StepWatch : une requête à l’API publique des versions "
                            "sur GitHub. Aucune capture ni donnée de l’intervention n’est envoyée.")
        general.addStretch(1)

        recording = self._page("Enregistrement")
        self._check(recording, "compact", "Réduire StepWatch en barre flottante pendant l’enregistrement")
        position = QHBoxLayout()
        position.setContentsMargins(24, 0, 0, 8)
        position.addWidget(QLabel("Position de la barre :"))
        self.bar_combo = QComboBox()
        self.bar_combo.addItems(list(BAR_POSITIONS.values()))
        self.vars["bar_position"].bind_combo(self.bar_combo)
        position.addWidget(self.bar_combo)
        position.addStretch(1)
        recording.addLayout(position)
        hotkeys = self._check(recording, "global_hotkeys",
                              "Raccourcis Ctrl+Maj+F9 / F10 / F11 actifs dans toutes les applications")
        if sys.platform != "win32":
            hotkeys.setEnabled(False)
            self._hint(recording, "Sous Linux, ces raccourcis fonctionnent dans les fenêtres de StepWatch.")
        self._check(recording, "sound", "Signal sonore à chaque étape enregistrée")
        recording.addStretch(1)

        guide = self._page("Guide et rapports")
        self._check(guide, "open_guide_after_stop", "Ouvrir l’onglet Guide à l’arrêt de l’enregistrement")
        self._check(guide, "auto_standalone", "Créer le rapport autonome automatiquement à l’arrêt")
        self._check(guide, "confirm_delete", "Demander confirmation avant de supprimer une étape")
        self._check(guide, "keep_retired", "Conserver la capture d’une étape supprimée (captures/retirees/)")
        self._hint(guide, "Décoché : la capture est effacée définitivement et la suppression ne peut pas être "
                          "annulée. StepWatch demande alors toujours confirmation.")
        guide.addStretch(1)

        interface = self._page("Interface")
        theme = QHBoxLayout()
        theme.addWidget(QLabel("Thème :"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEME_LABELS.values()))
        self.vars["theme"].bind_combo(self.theme_combo)
        theme.addWidget(self.theme_combo)
        theme.addStretch(1)
        interface.addLayout(theme)
        size = QHBoxLayout()
        size.addWidget(QLabel("Taille du texte :"))
        self.size_combo = QComboBox()
        self.size_combo.addItems([f"{value} %" for value in CHOICES["font_scale"]])
        self.vars["font_scale"].bind_combo(self.size_combo)
        size.addWidget(self.size_combo)
        size.addStretch(1)
        interface.addLayout(size)
        self._hint(interface, "Le thème et la taille du texte s’appliquent immédiatement.")
        self._check(interface, "advanced_open", "Afficher les réglages avancés au démarrage")
        self._check(interface, "tray_icon", "Icône dans la zone de notification (démarrer, pause, arrêt)")
        interface.addStretch(1)

        self._build_sync_page()

        buttons = QHBoxLayout()
        reset = QPushButton("Réinitialiser")
        reset.clicked.connect(self._reset)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.save_button = set_role(QPushButton("Enregistrer"), "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._apply)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.name_entry.setFocus()

    # ----- Synchronisation -------------------------------------------------------

    def _password_field(self) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def _build_sync_page(self) -> None:
        page = self._page("Synchronisation")
        kind = QHBoxLayout()
        kind.addWidget(QLabel("Destination :"))
        self.sync_combo = QComboBox()
        self.sync_combo.addItems(list(SYNC_LABELS.values()))
        self.vars["sync_kind"].bind_combo(self.sync_combo)
        kind.addWidget(self.sync_combo)
        kind.addStretch(1)
        page.addLayout(kind)
        self.sync_pages = QStackedWidget()
        self.sync_pages.addWidget(QWidget())  # Désactivée.

        webdav = QWidget()
        form = QFormLayout(webdav)
        url = self.vars["sync_url"].bind_line(QLineEdit())
        url.setPlaceholderText("https://cloud.exemple.fr/remote.php/dav/files/utilisateur/StepWatch")
        url.setMinimumWidth(420)
        form.addRow("Adresse WebDAV", url)
        self.webdav_user = self.vars["sync_user"].bind_line(QLineEdit())
        form.addRow("Utilisateur", self.webdav_user)
        self.webdav_password = self._password_field()
        form.addRow("Mot de passe", self.webdav_password)
        self._hint_form(form, "Nextcloud : créez un mot de passe d’application (Paramètres personnels › Sécurité). "
                              "Synology, QNAP : activez le serveur WebDAV en HTTPS.")
        self.sync_pages.addWidget(webdav)

        folder = QWidget()
        form = QFormLayout(folder)
        row = QHBoxLayout()
        row.addWidget(self.vars["sync_folder"].bind_line(QLineEdit()), 1)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(lambda: self._choose_into("sync_folder", "Dossier réseau de synchronisation"))
        row.addWidget(browse)
        form.addRow("Dossier", row)
        self._hint_form(form, "Un partage déjà accessible : \\\\nas\\interventions sous Windows, ou un partage "
                              "monté sous Linux (/mnt/nas/interventions). L’accès utilise les identifiants du système.")
        self.sync_pages.addWidget(folder)

        sftp = QWidget()
        form = QFormLayout(sftp)
        form.addRow("Serveur", self.vars["sync_host"].bind_line(QLineEdit()))
        port = self.vars["sync_port"].bind_line(QLineEdit())
        port.setMaximumWidth(90)
        form.addRow("Port", port)
        self.sftp_user = QLineEdit()
        # L'utilisateur est commun aux deux formulaires (une seule préférence).
        self.vars["sync_user"].bind_line(self.sftp_user)
        form.addRow("Utilisateur", self.sftp_user)
        remote = self.vars["sync_remote"].bind_line(QLineEdit())
        remote.setPlaceholderText("/volume1/interventions")
        form.addRow("Dossier distant", remote)
        self.sftp_password = self._password_field()
        self.sftp_password.setPlaceholderText("Facultatif avec une clé SSH")
        form.addRow("Mot de passe", self.sftp_password)
        self._hint_form(form, "À la première connexion, StepWatch affiche l’empreinte du serveur : comparez-la "
                              "avant de l’accepter. Une empreinte modifiée ensuite est refusée.")
        self.sync_pages.addWidget(sftp)
        page.addWidget(self.sync_pages)

        self._check(page, "sync_auto", "Synchroniser automatiquement le guide à l’arrêt de l’enregistrement")
        test = QHBoxLayout()
        self.test_button = QPushButton("Tester la connexion")
        self.test_button.clicked.connect(self.test_connection)
        test.addWidget(self.test_button)
        self.test_status = set_role(QLabel(""), "muted")
        self.test_status.setWordWrap(True)
        test.addWidget(self.test_status, 1)
        page.addLayout(test)
        self._hint(page, "Les captures peuvent contenir des données du client : choisissez un serveur de confiance. "
                         "Les mots de passe sont rangés dans le coffre du système (Gestionnaire d’identification "
                         "Windows, trousseau GNOME ou KWallet), jamais dans settings.json.")
        page.addStretch(1)
        self.sync_combo.currentTextChanged.connect(self._show_sync_page)
        self._show_sync_page()

    def _hint_form(self, form: QFormLayout, text: str) -> None:
        label = set_role(QLabel(text), "muted")
        label.setWordWrap(True)
        label.setMaximumWidth(520)
        form.addRow(label)

    def _show_sync_page(self, *_args) -> None:
        kind = self.sync_kind()
        self.sync_pages.setCurrentIndex(["", "webdav", "folder", "sftp"].index(kind))
        config = self.sync_config()
        stored = config is not None and config.kind != "folder" and sync.get_password(config) is not None
        for field in (self.webdav_password, self.sftp_password):
            if stored:
                field.setPlaceholderText("Enregistré dans le coffre du système")
        self.test_button.setEnabled(bool(kind))

    def sync_kind(self) -> str:
        labels = {label: key for key, label in SYNC_LABELS.items()}
        # Lu sur la liste : currentIndexChanged arrive avant la mise à jour de la valeur liée.
        return labels.get(self.sync_combo.currentText(), "")

    def sync_password(self) -> str:
        kind = self.sync_kind()
        field = self.webdav_password if kind == "webdav" else self.sftp_password if kind == "sftp" else None
        return field.text() if field is not None else ""

    def sync_config(self):
        values = self.values()
        return sync.config_from_preferences(values)

    def test_connection(self) -> None:
        config = self.sync_config()
        if config is None:
            return
        password = self.sync_password() or (sync.get_password(config) if config.kind != "folder" else None)
        self.test_button.setEnabled(False)
        self.test_status.setText("Connexion en cours…")

        def work():
            try:
                self._test_events.put(("ok", sync.test_connection(config, password)))
            except sync.UnknownHostKey as exc:
                self._test_events.put(("host", exc))
            except sync.SyncError as exc:
                self._test_events.put(("error", str(exc)))
            except Exception as exc:
                self._test_events.put(("error", f"Échec : {exc}"))

        threading.Thread(target=work, name="StepWatch-test-sync", daemon=True).start()
        self._test_timer.start()

    def _poll_test(self) -> None:
        try:
            kind, value = self._test_events.get_nowait()
        except queue.Empty:
            return
        self._test_timer.stop()
        self.test_button.setEnabled(True)
        if kind == "host":
            if messagebox.askyesno(
                    "Serveur SFTP inconnu",
                    f"Première connexion à {value.host}:{value.port}.\n\nEmpreinte {value.key_type} :\n"
                    f"{value.fingerprint}\n\nComparez-la avec celle du serveur (ssh-keygen -lf …). "
                    "Faire confiance à ce serveur ?", parent=self):
                sync.trust_host_key(value, sync.known_hosts_path())
                self.test_connection()
                return
            self.test_status.setText("Empreinte refusée : aucune connexion établie.")
            return
        self.test_status.setText(value)

    def _choose_into(self, key: str, title: str) -> None:
        selected = filedialog.askdirectory(parent=self, title=title)
        if selected:
            self.vars[key].set(selected)

    # ----- Construction ------------------------------------------------------

    def _page(self, title: str) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)
        self.tabs.addTab(page, title)
        return layout

    def _check(self, layout: QVBoxLayout, key: str, text: str) -> QCheckBox:
        box = self.vars[key].bind_check(QCheckBox(text))
        layout.addWidget(box)
        return box

    def _hint(self, layout: QVBoxLayout, text: str) -> None:
        label = set_role(QLabel(text), "muted")
        label.setWordWrap(True)
        label.setMaximumWidth(520)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(label)

    def _update_preview(self) -> None:
        self.preview.setText(f"{{date}} et {{heure}} sont remplacés. Exemple : « "
                             f"{expand_name(self.vars['name_template'].get())} »")

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Dossier des guides")
        if selected:
            self.vars["output"].set(selected)

    # ----- Résultat ------------------------------------------------------------

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {key: variable.get() for key, variable in self.vars.items()}
        positions = {label: key for key, label in BAR_POSITIONS.items()}
        result["bar_position"] = positions.get(result["bar_position"], "bas")
        themes = {label: key for key, label in THEME_LABELS.items()}
        result["theme"] = themes.get(result["theme"], "systeme")
        kinds = {label: key for key, label in SYNC_LABELS.items()}
        result["sync_kind"] = kinds.get(result["sync_kind"], "")
        try:
            result["font_scale"] = int(str(result["font_scale"]).rstrip(" %"))
        except ValueError:
            result["font_scale"] = self._initial_scale
        return result

    def _apply(self) -> None:
        values = self.values()
        # Jamais enregistré dans settings.json : l'application le range dans le coffre du système.
        values["sync_password"] = self.sync_password()
        self._on_apply(values)
        self.accept()

    def _reset(self) -> None:
        self._on_reset()
        self.accept()
