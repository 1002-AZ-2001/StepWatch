"""Fenêtre « Paramètres » : préférences de StepWatch regroupées par thème."""

from __future__ import annotations

import sys
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from .qtcompat import Value, filedialog
from .settings import CHOICES, expand_name
from .theme import set_role

BAR_POSITIONS = {"bas": "En bas de l’écran", "haut": "En haut de l’écran"}
THEME_LABELS = {"systeme": "Comme le système", "clair": "Clair", "sombre": "Sombre"}


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
        self._initial_scale = values["font_scale"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        self.tabs = QTabWidget()
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
        try:
            result["font_scale"] = int(str(result["font_scale"]).rstrip(" %"))
        except ValueError:
            result["font_scale"] = self._initial_scale
        return result

    def _apply(self) -> None:
        self._on_apply(self.values())
        self.accept()

    def _reset(self) -> None:
        self._on_reset()
        self.accept()
