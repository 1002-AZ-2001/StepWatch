"""Interface française (Qt). Tous les appels Qt restent sur le thread principal.

Les traitements longs (enregistrement, import, export, relecture, mises à
jour) tournent sur d'autres threads et communiquent par des files d'attente
lues par une minuterie Qt (``_poll``).
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QTimer, Qt, qVersion
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSystemTrayIcon, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from . import updater
from .engine import Recorder
from .floating import FloatingBar, StatusDot
from .hotkeys import LABELS as HOTKEY_LABELS, GlobalHotkeys
from .imaging import preview_image
from .library import recent_guides
from .models import CaptureConfig, CaptureTarget, Rect
from .platform import capture_backend_factory, exclude_from_capture
from .qtcompat import Value, application, filedialog, messagebox, simpledialog, to_pixmap
from .review import ImageView, SessionReview
from .settings import Preferences, default_path as settings_path, expand_name
from .settings_dialog import SettingsDialog
from .storage import SessionStore, export_markdown, export_standalone, import_sharex
from .theme import (PALETTE, STATUS_COLOURS, is_dark, pick_fonts, qicon, qpalette, render_icon, set_role, stylesheet,
                    use_palette)

LOGGER = logging.getLogger(__name__)
DOCUMENTATION_URL = "https://github.com/1002-AZ-2001/StepWatch#readme"
ISSUES_URL = "https://github.com/1002-AZ-2001/StepWatch/issues"
#: Préférences modifiables dans la fenêtre Paramètres.
SETTINGS_KEYS = ("name_template", "output", "compact", "bar_position", "global_hotkeys", "sound",
                 "open_guide_after_stop", "auto_standalone", "confirm_delete", "keep_retired",
                 "font_scale", "advanced_open", "check_updates", "theme", "tray_icon")
#: Mêmes combinaisons que les raccourcis globaux, dans les fenêtres de StepWatch.
LOCAL_HOTKEYS = {"pause": "Ctrl+Shift+F9", "note": "Ctrl+Shift+F10", "stop": "Ctrl+Shift+F11"}
BASE_FONT_SIZE = 10


class MainWindow(QMainWindow):
    """Fenêtre principale ; la fermeture passe par l'application (arrêt propre)."""

    def __init__(self, app: "StepWatchApp"):
        super().__init__()
        self._app = app
        self.allow_close = False

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        QTimer.singleShot(0, self._app._close)


class StepWatchApp:
    """Fenêtre principale ; les enregistrements ne démarrent que sur demande."""

    VISUAL_MODE = "Changements visuels uniquement"
    MODES = {
        "Hybride · clics et changements": "hybrid",
        "Clics locaux uniquement": "clicks",
        VISUAL_MODE: "visual",
    }
    SOURCES = ("Fenêtre", "Moniteur", "Zone personnalisée")
    ACTIVE_STATES = {"starting", "recording", "paused", "stopping"}
    STATE_NAMES = {
        "idle": "Prêt",
        "starting": "Démarrage de l’enregistrement…",
        "recording": "Enregistrement en cours",
        "paused": "En pause",
        "stopping": "Arrêt en cours…",
        "stopped": "Terminé",
        "error": "Erreur · consulter le journal",
    }

    def __init__(self, log_path: Path | None = None, preferences: Preferences | None = None) -> None:
        self.qapp = application()
        # Sans préférences (tests, auto-test), rien n'est lu ni écrit sur disque.
        self._prefs = preferences if preferences is not None else Preferences.memory()
        self._ui_thread = threading.get_ident()
        self._disposed = False
        self.quit_on_dispose = False
        self.root = MainWindow(self)
        self.root.setWindowTitle("StepWatch — Documentation d’intervention")
        families = []
        try:
            from PySide6.QtGui import QFontDatabase
            families = QFontDatabase.families()
        except Exception:
            pass
        self._ui_font, self._mono_font = pick_fonts(families)
        self._backend_factory = capture_backend_factory()
        self._backend = self._backend_factory() if self._backend_factory is not None else None
        self._uses_portal = getattr(self._backend, "uses_portal", False) is True
        self._recorder: Recorder | None = None
        self._targets: list[CaptureTarget] = []
        self._listed_source: str | None = None
        self._needs_target_selection = False
        self._last_image: Image.Image | None = None
        self._session_dir: Path | None = None
        self._import_thread: threading.Thread | None = None
        self._export_thread: threading.Thread | None = None
        self._background_events: queue.Queue = queue.Queue()
        self._closing = False
        self._close_error: str | None = None
        self._settings_widgets: list[QWidget] = []
        self._controls_key = None
        self._last_state = "idle"
        self._bar: FloatingBar | None = None
        self._bar_excluded = False
        self._compact_session = False
        self._withdrawn = False
        self._regions_key = None
        self._hotkeys = GlobalHotkeys() if GlobalHotkeys.supported() else None
        self._hotkeys_on = False
        self._update_thread: threading.Thread | None = None
        self._pending_release: updater.Release | None = None
        self._update_dialog = None
        self._settings_dialog = None
        self._tray: QSystemTrayIcon | None = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)

        self.name = Value("Nouvelle intervention")
        self.output = Value(str(Path.home() / "Documents" / "StepWatch"))
        self.source = Value(self.SOURCES[0])
        self.mode = Value(self.VISUAL_MODE if self._uses_portal else next(iter(self.MODES)))
        self.interval = Value("1,0")
        self.delay = Value("0,35")
        self.sensitivity = Value("2,0")
        self.conservative = Value(False)
        self.compact = Value(True)
        self.region_values = [Value(value) for value in ("0", "0", "1280", "720")]
        self.state_text = Value("Prêt · aucun enregistrement actif")
        self.kept = Value("0")
        self.ignored = Value("0")
        self.session_text = Value("Le dossier et le rapport sont créés au démarrage.")
        self._advanced_open = Value(False)

        self.apply_theme()
        self._build()
        self._fit_to_screen()
        self._apply_preferences()
        self._build_menu()
        self._local_hotkeys = []
        for action, sequence in LOCAL_HOTKEYS.items():
            shortcut = QShortcut(QKeySequence(sequence), self.root)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda action=action: self._hotkey(action))
            self._local_hotkeys.append(shortcut)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _scheme: self._prefs.values["theme"] == "systeme"
                                             and self.apply_theme())
        self._sync_tray()
        if log_path is not None:
            self._write_log(f"Journal de diagnostic : {log_path}", level=None)
        self._refresh_targets()
        self._poll()

    # ----- Apparence ------------------------------------------------------------

    def _system_dark(self) -> bool:
        try:
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except AttributeError:
            return False

    def apply_theme(self) -> None:
        """Palette (claire, sombre, système) et taille du texte des Paramètres."""
        values = self._prefs.values
        use_palette(is_dark(values["theme"], self._system_dark()))
        self.qapp.setStyle("Fusion")
        self.qapp.setPalette(qpalette())
        size = BASE_FONT_SIZE * values["font_scale"] / 100
        self.qapp.setStyleSheet(stylesheet(self._ui_font, size))
        self.qapp.setWindowIcon(qicon())
        if hasattr(self, "_status"):
            self._set_status_colours(self._status_state)
            self.log.setStyleSheet(f"font-family: '{self._mono_font}'; color: {PALETTE['log_text']};")
            self.masks_text.setStyleSheet(f"font-family: '{self._mono_font}';")

    def _fit_to_screen(self, area=None) -> None:
        """Taille par défaut, bornée par l'écran principal (portables 1366 × 768)."""
        if area is None:
            screen = QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen is not None else None
        width, height = 1190, 820
        if area is not None:
            width, height = min(width, area.width() - 20), min(height, area.height() - 40)
            self.root.setMinimumSize(min(1000, area.width() - 20), min(700, area.height() - 40))
            self.root.resize(width, height)
            self.root.move(area.x() + max(0, (area.width() - width) // 2),
                           area.y() + max(0, (area.height() - height) // 2))
        else:
            self.root.resize(width, height)

    @staticmethod
    def _icon_pixmap(size: int):
        pixmap = to_pixmap(render_icon(size * 2))
        pixmap.setDevicePixelRatio(2)
        return pixmap

    # ----- Construction -------------------------------------------------------------

    def _setting(self, widget: QWidget) -> QWidget:
        self._settings_widgets.append(widget)
        return widget

    def _section(self, parent_layout: QVBoxLayout, title: str) -> QGridLayout:
        """Carte avec un titre de section discret, comme un groupe de réglages."""
        card = set_role(QFrame(), "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(set_role(QLabel(title.upper()), "section"))
        body = QGridLayout()
        body.setHorizontalSpacing(8)
        body.setVerticalSpacing(6)
        layout.addLayout(body)
        parent_layout.addWidget(card)
        return body

    def _muted(self, text: str) -> QLabel:
        label = set_role(QLabel(text), "muted")
        label.setWordWrap(True)
        return label

    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("Page")
        self.root.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # En-tête : identité, état de l'enregistrement, actions de session.
        heading = set_role(QWidget(), "header")
        heading.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header = QHBoxLayout(heading)
        header.setContentsMargins(20, 10, 20, 10)
        logo = QLabel()
        logo.setPixmap(self._icon_pixmap(34))
        header.addWidget(logo)
        header.addWidget(set_role(QLabel("StepWatch"), "title"))
        header.addSpacing(12)
        self._status = QFrame()
        self._status.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        status_layout = QHBoxLayout(self._status)
        status_layout.setContentsMargins(10, 3, 12, 3)
        status_layout.setSpacing(6)
        self._status_dot = StatusDot(10)
        status_layout.addWidget(self._status_dot)
        self._status_label = self.state_text.bind_label(QLabel())
        self._status_label.setStyleSheet("font-weight: 700;")
        status_layout.addWidget(self._status_label)
        header.addWidget(self._status)
        header.addStretch(1)
        self.open_session_button = QToolButton()
        self.open_session_button.setText("Ouvrir un guide")
        self.open_session_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._recent_menu = QMenu(self.open_session_button)
        self._recent_menu.aboutToShow.connect(lambda: self._fill_recent_menu(self._recent_menu))
        self.open_session_button.setMenu(self._recent_menu)
        header.addWidget(self.open_session_button)
        self.import_button = QPushButton("Importer ShareX")
        self.import_button.clicked.connect(self._import)
        header.addWidget(self.import_button)
        outer.addWidget(heading)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 14, 16, 10)
        content_layout.setSpacing(12)
        outer.addWidget(content, 1)

        # Barre d'enregistrement : l'action principale reste toujours visible.
        toolbar = set_role(QFrame(), "card")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(12, 10, 16, 10)
        self.start_button = set_role(QPushButton("●  Enregistrer"), "record")
        self.start_button.clicked.connect(self._start)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self._pause)
        self.resume_button = QPushButton("Reprendre")
        self.resume_button.clicked.connect(self._resume)
        self.stop_button = QPushButton("■  Arrêter")
        self.stop_button.clicked.connect(self._stop)
        self.note_button = QPushButton("+  Note")
        self.note_button.clicked.connect(self._add_note)
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button, self.note_button):
            tools.addWidget(button)
        tools.addStretch(1)
        for label, variable in (("étapes", self.kept), ("ignorées", self.ignored)):
            tools.addSpacing(12)
            tools.addWidget(set_role(variable.bind_label(QLabel()), "count"))
            tools.addWidget(set_role(QLabel(label), "muted"))
        content_layout.addWidget(toolbar)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        content_layout.addWidget(self._tabs, 1)

        # Onglet Enregistrer : réglages à gauche, aperçu à droite.
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 12, 0, 0)
        body_layout.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(430)
        scroll.setMaximumWidth(470)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(10)
        scroll.setWidget(left)
        body_layout.addWidget(scroll)

        intervention = self._section(left_layout, "Intervention")
        intervention.addWidget(QLabel("Nom du guide"), 0, 0, 1, 2)
        intervention.addWidget(self._setting(self.name.bind_line(QLineEdit())), 1, 0, 1, 2)
        intervention.addWidget(QLabel("Dossier des guides"), 2, 0, 1, 2)
        intervention.addWidget(self._setting(self.output.bind_line(QLineEdit())), 3, 0)
        browse = self._setting(QPushButton("Parcourir…"))
        browse.clicked.connect(self._choose_output)
        intervention.addWidget(browse, 3, 1)
        intervention.setColumnStretch(0, 1)

        selection = self._section(left_layout, "Que capturer ?")
        if self._uses_portal:
            selection.addWidget(self._muted("Le portail Wayland vous propose de choisir un écran ou une fenêtre "
                                            "au démarrage."), 0, 0)
        else:
            self._build_target_choices(selection)

        capture = self._section(left_layout, "Déclenchement")
        if self._uses_portal:
            capture.addWidget(QLabel(self.VISUAL_MODE), 0, 0)
        else:
            self.mode_combo = QComboBox()
            self.mode_combo.addItems(list(self.MODES))
            capture.addWidget(self._setting(self.mode.bind_combo(self.mode_combo)), 0, 0)
        capture.addWidget(self._muted("Les clics et les changements visibles deviennent des étapes. "
                                      "Aucune frappe clavier n’est enregistrée."), 1, 0)
        self.compact_check = self._setting(self.compact.bind_check(
            QCheckBox("Barre flottante pendant l’enregistrement")))
        capture.addWidget(self.compact_check, 2, 0)

        # Réglages avancés repliés par défaut : la plupart des interventions s'en passent.
        self.advanced_button = set_role(QPushButton("▸  Réglages avancés"), "link")
        self.advanced_button.clicked.connect(self._toggle_advanced)
        left_layout.addWidget(self.advanced_button)
        self._advanced = QWidget()
        advanced_layout = QVBoxLayout(self._advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(10)
        timing = self._section(advanced_layout, "Détection")
        fields = [("Intervalle (s)", self.interval)]
        if not self._uses_portal:
            fields.append(("Délai clic (s)", self.delay))
        fields.append(("Seuil (%)", self.sensitivity))
        for column, (title, variable) in enumerate(fields):
            timing.addWidget(QLabel(title), 0, column)
            timing.addWidget(self._setting(variable.bind_line(QLineEdit())), 1, column)
        self.conservative_check = self._setting(self.conservative.bind_check(
            QCheckBox("Mode conservateur · détecter les petits changements")))
        timing.addWidget(self.conservative_check, 2, 0, 1, 3)
        timing.addWidget(self._muted("Un seuil plus bas conserve davantage de changements visuels."), 3, 0, 1, 3)
        masks = self._section(advanced_layout, "Masquage des informations sensibles")
        masks.addWidget(self._muted("Une zone par ligne : x, y, largeur, hauteur, depuis le coin supérieur gauche "
                                    "de la capture. Après l’enregistrement, l’éditeur du guide permet aussi de "
                                    "masquer une zone à la souris."), 0, 0)
        self.masks_text = self._setting(QPlainTextEdit())
        self.masks_text.setFixedHeight(76)
        self.masks_text.setPlaceholderText("0, 0, 400, 60")
        masks.addWidget(self.masks_text, 1, 0)
        left_layout.addWidget(self._advanced)
        self._advanced.hide()
        left_layout.addStretch(1)

        preview_card = set_role(QFrame(), "card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.addWidget(set_role(QLabel("Dernière capture"), "cardtitle"))
        self._empty_preview = (
            "L’aperçu apparaîtra après la première étape.\nPendant l’enregistrement, StepWatch se réduit en une "
            "petite barre flottante.\n\nCtrl+Maj+F9 pause/reprise · Ctrl+Maj+F10 note · Ctrl+Maj+F11 arrêt"
            + (" (dans toutes les applications)" if sys.platform == "win32" else "")
            if self._backend is not None
            else "Importez un dossier ShareX ou ouvrez un guide pour le modifier dans l’onglet Guide.")
        self.preview = ImageView(self._empty_preview)
        preview_layout.addWidget(self.preview, 1)
        body_layout.addWidget(preview_card, 1)
        self._tabs.addTab(body, "Enregistrer")

        self._review = SessionReview(choose_session=self._choose_session, on_loaded=self._use_session,
                                     on_error=self._review_error, on_changed=self._guide_changed,
                                     export=self._export_from_guide, options=lambda: self._prefs.values)
        self._tabs.addTab(self._review, "Guide")

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(0, 12, 0, 0)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1200)
        log_layout.addWidget(self.log)
        self._tabs.addTab(log_page, "Journal")
        self._tabs.currentChanged.connect(self._tab_changed)

        session = set_role(QFrame(), "card")
        session_layout = QHBoxLayout(session)
        session_layout.setContentsMargins(14, 8, 12, 8)
        location = set_role(self.session_text.bind_label(QLabel()), "muted")
        location.setWordWrap(True)
        location.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        session_layout.addWidget(location, 1)
        self.open_button = QPushButton("Ouvrir le dossier")
        self.open_button.clicked.connect(self._open_session)
        self.export_button = QPushButton("Rapport autonome")
        self.export_button.clicked.connect(self._export_standalone)
        session_layout.addWidget(self.open_button)
        session_layout.addWidget(self.export_button)
        content_layout.addWidget(session)

        footer = "Les fenêtres capturées doivent rester visibles. Les masques sont appliqués avant l’enregistrement."
        if self._uses_portal:
            footer = "La source est partagée avec votre accord. Les masques sont appliqués avant l’enregistrement."
        elif self._backend is None:
            footer = "Import ShareX, guides et rapports disponibles. La capture nécessite Windows, X11 ou Wayland."
        content_layout.addWidget(self._muted(footer))
        self._status_state = "idle"
        self._set_status_colours("idle")
        self.log.setStyleSheet(f"font-family: '{self._mono_font}'; color: {PALETTE['log_text']};")
        self.masks_text.setStyleSheet(f"font-family: '{self._mono_font}';")

    def _build_target_choices(self, selection: QGridLayout) -> None:
        self.source_combo = QComboBox()
        self.source_combo.addItems(list(self.SOURCES))
        self.source.bind_combo(self.source_combo)
        self.source_combo.activated.connect(lambda _index: self._refresh_targets())
        selection.addWidget(self._setting(self.source_combo), 0, 0)
        self.refresh_button = self._setting(QPushButton("Actualiser"))
        self.refresh_button.clicked.connect(self._refresh_targets)
        selection.addWidget(self.refresh_button, 0, 1)
        self.target_combo = self._setting(QComboBox())
        self.target_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target_combo.setMinimumContentsLength(20)
        selection.addWidget(self.target_combo, 1, 0, 1, 2)
        self.region_frame = QWidget()
        region = QGridLayout(self.region_frame)
        region.setContentsMargins(0, 0, 0, 0)
        for index, label in enumerate(("X", "Y", "Largeur", "Hauteur")):
            region.addWidget(QLabel(label), 0, index)
            region.addWidget(self._setting(self.region_values[index].bind_line(QLineEdit())), 1, index)
        selection.addWidget(self.region_frame, 2, 0, 1, 2)
        selection.addWidget(self._muted("Coordonnées en pixels physiques ; X et Y peuvent être négatifs."),
                            3, 0, 1, 2)
        selection.setColumnStretch(0, 1)

    def _toggle_advanced(self) -> None:
        self._show_advanced(not self._advanced_open.get())

    def _show_advanced(self, opened: bool) -> None:
        self._advanced_open.set(opened)
        self._advanced.setVisible(opened)
        self.advanced_button.setText(("▾" if opened else "▸") + "  Réglages avancés")

    def _set_status_colours(self, state: str) -> None:
        self._status_state = state
        dot, background, text = STATUS_COLOURS.get(state, STATUS_COLOURS["idle"])
        self._status.setStyleSheet(f"QFrame {{ background: {background}; border-radius: 11px; }}")
        self._status_label.setStyleSheet(f"font-weight: 700; color: {text}; background: transparent;")
        self._status_dot.set_colour(dot)

    def _write_log(self, message: str, level: int | None = logging.INFO) -> None:
        """Ajoute une ligne au journal de l'interface et, si level, au journal de diagnostic."""
        from datetime import datetime

        if level is not None:
            LOGGER.log(level, message)
        self.log.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")

    # ----- Cibles -----------------------------------------------------------------

    def _set_target_message(self, text: str) -> None:
        self.target_combo.setCurrentIndex(-1)
        self.target_combo.setPlaceholderText(text)

    def target_text(self) -> str:
        return self.target_combo.currentText() or self.target_combo.placeholderText()

    def _refresh_targets(self) -> None:
        if self._active() or self._backend is None or self._uses_portal:
            return
        source = self.source.get()
        custom = source == "Zone personnalisée"
        index = self.target_combo.currentIndex()
        previous = None
        if source != self._listed_source:
            self._listed_source = source
            self._needs_target_selection = False
        elif 0 <= index < len(self._targets):
            previous = self._targets[index]
        self.region_frame.setVisible(custom)
        self.target_combo.setEnabled(not custom)
        self.refresh_button.setEnabled(not custom)
        if custom:
            self._targets = []
            self.target_combo.clear()
            self._set_target_message("Coordonnées personnalisées")
            self._update_controls()
            return
        try:
            # L'énumération des fenêtres est indépendante de celle des moniteurs.
            self._targets = self._backend.list_windows() if source == "Fenêtre" else self._backend.list_monitors()
        except Exception as exc:
            self._targets = []
            self._write_log(f"Énumération impossible : {exc}")
        labels = [f"{index + 1}. {target.label}" for index, target in enumerate(self._targets)]
        self.target_combo.clear()
        self.target_combo.addItems(labels)
        self.target_combo.setCurrentIndex(-1)
        if previous is not None:
            for index, target in enumerate(self._targets):
                if previous.kind == target.kind and (previous.hwnd == target.hwnd if target.kind == "window"
                                                     else previous.rect == target.rect):
                    self.target_combo.setCurrentIndex(index)
                    self._needs_target_selection = False
                    break
            else:
                self._needs_target_selection = True
        elif labels and not self._needs_target_selection:
            self.target_combo.setCurrentIndex(0)
        if not labels:
            self._set_target_message("Aucune cible disponible")
            self._write_log("Aucune cible. Actualisez la liste après avoir ouvert une fenêtre visible.")
        elif self._needs_target_selection:
            self._set_target_message("Sélectionnez une cible")
            if previous is not None:
                self._write_log("La cible sélectionnée a disparu. Choisissez une nouvelle cible.")
        self._update_controls()

    def _choose_output(self) -> None:
        current = self.output.get()
        selected = filedialog.askdirectory(parent=self.root, title="Dossier des interventions",
                                           initialdir=current if Path(current).is_dir() else None)
        if selected:
            self.output.set(selected)

    def _choose_session(self) -> None:
        if self._active() or self._background_running() or self._closing:
            return
        selected = filedialog.askdirectory(parent=self.root, title="Choisir un dossier contenant steps.json")
        if selected:
            self._review.load(Path(selected))
            self._update_controls()

    def _fill_recent_menu(self, menu: QMenu | None = None) -> None:
        """Guides récents : dossiers déjà ouverts et interventions du dossier des guides."""
        from datetime import datetime

        menu = self._recent_menu if menu is None else menu
        menu.clear()
        remembered = self._prefs.values["recent"]
        entries = recent_guides(self.output.get().strip() or None, remembered)
        if entries:
            title = menu.addAction("Guides récents")
            title.setEnabled(False)
            for entry in entries:
                when = datetime.fromtimestamp(entry.modified).strftime("%d/%m/%Y %H:%M")
                menu.addAction(f"{entry.name}  ·  {when}",
                               lambda directory=entry.directory: self._open_guide(directory))
            menu.addSeparator()
        menu.addAction("Parcourir…", self._choose_session)

    def _open_guide(self, directory: Path) -> None:
        if self._active() or self._background_running() or self._closing:
            return
        self._review.load(Path(directory))
        self._tabs.setCurrentWidget(self._review)
        self._update_controls()

    def _use_session(self, directory: Path) -> None:
        self._session_dir = directory
        self.session_text.set(str(directory))
        self._prefs.remember_guide(directory)
        self._prefs.save()

    # ----- Préférences ----------------------------------------------------------

    def _apply_preferences(self, theme: bool = False) -> None:
        values = self._prefs.values
        self.name.set(expand_name(values["name_template"]))
        for variable, key in ((self.output, "output"), (self.interval, "interval"),
                              (self.delay, "delay"), (self.sensitivity, "sensitivity")):
            if values[key]:
                variable.set(values[key])
        self.conservative.set(values["conservative"])
        self.compact.set(values["compact"])
        if values["source"] in self.SOURCES and not self._uses_portal:
            self.source.set(values["source"])
        if values["mode"] in self.MODES and not self._uses_portal:
            self.mode.set(values["mode"])
        if values["masks"]:
            self.masks_text.setPlainText(values["masks"])
        if values["advanced_open"] != self._advanced_open.get():
            self._toggle_advanced()
        geometry = values["geometry"]
        match = re.fullmatch(r"(\d+)x(\d+)\+?(-?\d+)\+?(-?\d+)", geometry or "")
        if match:
            width, height, left, top = (int(part) for part in match.groups())
            screen = QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen is not None else None
            # Taille enregistrée sur un écran plus grand que l'actuel : ignorée.
            if area is not None and 0 < width <= area.width() and 0 < height <= area.height():
                self.root.resize(width, height)
                if QGuiApplication.screenAt(QPoint(left + 40, top + 40)) is not None:
                    self.root.move(left, top)
        if theme:
            self.apply_theme()

    def _store_preferences(self) -> None:
        values = self._prefs.values
        values.update(output=self.output.get(), interval=self.interval.get(), delay=self.delay.get(),
                      sensitivity=self.sensitivity.get(), conservative=self.conservative.get(),
                      compact=self.compact.get(), source=self.source.get(), mode=self.mode.get(),
                      masks=self.masks_text.toPlainText(), advanced_open=self._advanced_open.get())
        window = self.root
        if not self._withdrawn and window.isVisible() and not (window.isMaximized() or window.isMinimized()):
            frame = window.geometry()
            values["geometry"] = f"{frame.width()}x{frame.height()}+{frame.x()}+{frame.y()}"
        self._prefs.save()

    def _guide_changed(self, message: str) -> None:
        self._write_log(message)

    def _export_from_guide(self, kind: str) -> None:
        if kind == "standalone":
            self._export_standalone()
        elif kind == "folder":
            self._open_session()
        elif kind == "markdown" and self._session_dir is not None:
            try:
                path = export_markdown(self._session_dir)
            except Exception as exc:
                self._review_error(str(exc))
            else:
                self._write_log(f"Rapport Markdown : {path} (liens vers captures/).")

    def _review_error(self, message: str) -> None:
        self._write_log(f"Relecture impossible : {message}", level=logging.WARNING)
        if not self._closing:
            messagebox.showerror("Relecture impossible", message, parent=self.root)

    def _show_review(self) -> None:
        if self._session_dir is None or self._closing or self._active():
            return
        if not self._review.busy and self._review.directory != self._session_dir:
            self._review.load(self._session_dir)
        self._tabs.setCurrentWidget(self._review)

    def _tab_changed(self, _index: int) -> None:
        if self._tabs.currentWidget() is self._review:
            self._show_review()

    @staticmethod
    def _number(value: str, label: str) -> float:
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"{label} : nombre attendu, par exemple 1,5.") from None

    @staticmethod
    def _integer(value: str, label: str) -> int:
        try:
            return int(value.strip())
        except ValueError:
            raise ValueError(f"{label} : nombre entier de pixels attendu.") from None

    def _output_dir(self) -> Path:
        text = self.output.get().strip()
        if not text:
            raise ValueError("Choisissez un dossier pour les rapports.")
        directory = Path(text).expanduser()
        if not directory.is_absolute():
            # Relatif au dossier courant, qui dépend du raccourci de lancement.
            raise ValueError("Indiquez un chemin complet pour le dossier des rapports.")
        return directory

    def _read_config(self) -> tuple[CaptureTarget, CaptureConfig]:
        if self._uses_portal:
            target = CaptureTarget(kind="portal", label="Écran ou fenêtre via le portail")
        elif self.source.get() == "Zone personnalisée":
            names = ("X de la zone", "Y de la zone", "Largeur de la zone", "Hauteur de la zone")
            rect = Rect(*(self._integer(variable.get(), name)
                          for variable, name in zip(self.region_values, names)))
            target = CaptureTarget(kind="region", label="Zone personnalisée", rect=rect)
        else:
            index = self.target_combo.currentIndex()
            if index < 0 or index >= len(self._targets):
                raise ValueError("Sélectionnez une fenêtre ou un moniteur disponible.")
            target = self._targets[index]
        masks = []
        for line_number, line in enumerate(self.masks_text.toPlainText().splitlines(), 1):
            if not line.strip():
                continue
            fields = line.replace(";", ",").split(",")
            if len(fields) != 4:
                raise ValueError(f"Masque ligne {line_number} : indiquez x, y, largeur, hauteur.")
            values = [self._integer(field, f"Masque ligne {line_number}") for field in fields]
            try:
                mask = Rect(*values)
            except ValueError as exc:
                raise ValueError(f"Masque ligne {line_number} : {exc}") from None
            if mask.left < 0 or mask.top < 0:
                raise ValueError(f"Masque ligne {line_number} : X et Y doivent être positifs ou nuls.")
            masks.append(mask)
        config = CaptureConfig(
            mode="visual" if self._uses_portal else self.MODES[self.mode.get()],
            interval=self._number(self.interval.get(), "Intervalle de comparaison"),
            click_delay=0 if self._uses_portal else self._number(self.delay.get(), "Délai après clic"),
            sensitivity=self._number(self.sensitivity.get(), "Seuil de changement"),
            conservative=self.conservative.get(),
            masks=tuple(masks),
        )
        return target, config

    # ----- Enregistrement -----------------------------------------------------------

    def _active(self) -> bool:
        return self._recorder is not None and self._recorder.state in self.ACTIVE_STATES

    def _start(self) -> None:
        if self._active() or self._closing or self._background_running():
            return
        if self._recorder is not None:
            # Derniers événements de la session précédente (erreur de rapport
            # à l'arrêt, par exemple) : les afficher avant de la remplacer.
            self._drain(self._recorder.events, None, self._handle_event)
        store = None
        recorder = None
        try:
            if self._backend is None:
                raise RuntimeError("L’enregistrement est indisponible dans cette session. "
                                   "Vous pouvez importer un dossier ShareX.")
            if not self.name.get().strip():
                raise ValueError("Indiquez un nom d’intervention.")
            output = self._output_dir()
            target, config = self._read_config()
            # Validation préalable : ne crée pas de dossier pour une cible déjà fermée.
            if not self._uses_portal:
                self._backend.resolve(target)
            store = SessionStore(output, self.name.get().strip())
            recorder = Recorder(target, config, store, backend_factory=self._backend_factory)
            self._recorder = recorder
            self._review.clear()
            self._tabs.setCurrentIndex(0)
            self._use_session(store.directory)
            self.kept.set("0")
            self.ignored.set("0")
            self._last_image = None
            self.preview.set_message("En attente de la première capture…")
            recorder.start()
            self._compact_session = self.compact.get()
            self._regions_key = None
            self._store_preferences()
            self._write_log(f"Intervention : {self.name.get().strip()} — {target.label}")
            self._write_log(f"Rapport local : {store.directory}")
        except Exception as exc:
            if store is not None:
                if recorder is not None:
                    recorder.stop(timeout=0)
                else:
                    store.close()
            self._write_log(f"Démarrage impossible : {exc}")
            messagebox.showerror("Démarrage impossible", str(exc), parent=self.root)
        self._update_controls()

    def _pause(self) -> None:
        if self._recorder is not None:
            try:
                self._recorder.pause()
            except Exception as exc:
                self._write_log(f"Pause impossible : {exc}")
        self._update_controls()

    def _resume(self) -> None:
        if self._recorder is not None:
            try:
                self._recorder.resume()
            except Exception as exc:
                self._write_log(f"Reprise impossible : {exc}")
        self._update_controls()

    def _stop(self) -> None:
        if self._recorder is not None:
            self._recorder.stop(timeout=0)
        self._update_controls()

    def _add_note(self) -> None:
        """Note libre du technicien, ajoutée à la dernière étape conservée."""
        if self._recorder is None or self._recorder.state not in {"recording", "paused"}:
            return
        text = simpledialog.askstring(
            "Ajouter une note",
            "Note pour la dernière étape (aucune frappe n’est enregistrée hors de ce champ) :",
            parent=self._bar if self._bar is not None and self._withdrawn else self.root,
        )
        if not text or not text.strip():
            return
        try:
            self._recorder.add_note(text)
        except Exception as exc:
            self._write_log(f"Note non ajoutée : {exc}")

    def _after_recording_stopped(self) -> None:
        values = self._prefs.values
        if values["open_guide_after_stop"]:
            self._show_review()
        # Nom proposé pour le prochain guide (Paramètres › Général).
        self.name.set(expand_name(values["name_template"]))
        if values["auto_standalone"]:
            self._export_when_idle(attempts=100)
        if self._pending_release is not None:
            # Mise à jour trouvée pendant l'enregistrement : proposée maintenant.
            release, self._pending_release = self._pending_release, None
            self.after(500, lambda: self._show_update(release))

    def after(self, delay_ms: int, callback) -> None:
        """Appel différé, ignoré une fois l'interface libérée."""
        QTimer.singleShot(delay_ms, lambda: None if self._disposed else callback())

    def _export_when_idle(self, attempts: int) -> None:
        """Rapport autonome automatique, dès que la relecture du guide est terminée."""
        if self._closing or self._disposed or attempts <= 0:
            return
        if self._background_running() or self._session_dir is None:
            self.after(100, lambda: self._export_when_idle(attempts - 1))
            return
        self._export_standalone()

    def _background_running(self) -> bool:
        return self._review.busy or any(thread is not None and thread.is_alive()
                                        for thread in (self._import_thread, self._export_thread))

    def _update_controls(self) -> None:
        state = self._recorder.state if self._recorder is not None else "idle"
        active = state in self.ACTIVE_STATES
        busy = self._closing or self._background_running()
        if (self._last_state in self.ACTIVE_STATES and state == "stopped"
                and not self._closing):
            self._after_recording_stopped()
            busy = self._background_running()
        self._last_state = state
        key = state, busy, self._closing, self._session_dir, self.source.get()
        if key == self._controls_key:
            return
        self.start_button.setEnabled(not (active or busy or self._backend is None))
        self.pause_button.setEnabled(state == "recording" and not self._closing)
        self.resume_button.setEnabled(state == "paused" and not self._closing)
        self.stop_button.setEnabled(state in {"starting", "recording", "paused"} and not self._closing)
        self.note_button.setEnabled(state in {"recording", "paused"} and not self._closing)
        self.import_button.setEnabled(not (active or busy))
        self._review.open_button.setEnabled(not (active or busy))
        self.open_session_button.setEnabled(not (active or busy))
        other_work = self._closing or any(thread is not None and thread.is_alive()
                                          for thread in (self._import_thread, self._export_thread))
        self._review.set_editable(not active and not other_work)
        self._set_status_colours("stopping" if self._closing else state)
        self.open_button.setEnabled(self._session_dir is not None)
        exportable = self._session_dir is not None and not active and not busy
        self.export_button.setEnabled(exportable)
        for widget in self._settings_widgets:
            widget.setEnabled(not (active or busy))
        if not active and not busy and not self._uses_portal and self._backend is not None:
            custom = self.source.get() == "Zone personnalisée"
            self.target_combo.setEnabled(not custom)
            self.refresh_button.setEnabled(not custom)
        if self._closing:
            self.state_text.set("Fermeture en cours · finalisation des fichiers…")
        elif self._import_thread is not None and self._import_thread.is_alive():
            self.state_text.set("Import ShareX en cours…")
        elif self._export_thread is not None and self._export_thread.is_alive():
            self.state_text.set("Export du rapport autonome en cours…")
        elif state == "starting" and self._uses_portal:
            self.state_text.set("Choisissez l’écran ou la fenêtre à partager…")
        else:
            self.state_text.set(self.STATE_NAMES.get(state, state))
        self._update_tray(state)
        self._controls_key = key

    def _handle_event(self, event: dict) -> None:
        if "kept" in event:
            self.kept.set(str(event["kept"]))
        if "ignored" in event:
            self.ignored.set(str(event["ignored"]))
        if event.get("type") == "saved" and event.get("image") is not None:
            self._last_image = event["image"]
            self.preview.set_image(preview_image(self._last_image, (1600, 1000)))
            if self._prefs.values["sound"]:
                QApplication.beep()
        message = event.get("message", "")
        if message:
            if event.get("type") == "ignored" and message.startswith("Variation visuelle ignorée"):
                LOGGER.debug(message)  # Le compteur montre déjà les variations écartées.
                return
            level = {"error": logging.WARNING, "ignored": logging.DEBUG}.get(event.get("type"), logging.INFO)
            self._write_log(str(message), level=level)

    def _handle_background_event(self, event: dict) -> None:
        if event["type"] == "import_done":
            self._use_session(Path(event["directory"]))
            self._write_log(f"Import terminé. Sources préservées. Rapport : {self._session_dir}")
            self._show_review()
        elif event["type"] == "export_done":
            size = event["size"] / 1_000_000
            self._write_log(f"Rapport autonome : {event['path']} ({size:.1f} Mo). Il contient "
                            "toutes les captures : protégez-le comme le dossier de l’intervention.")
            if event.get("markdown"):
                self._write_log(f"Rapport Markdown : {event['markdown']} (liens vers captures/).")
        elif event["type"] == "update":
            self._update_checked(event)
        elif event["type"] == "selfcheck":
            self._write_log(event["message"])
            if not self._closing:
                (messagebox.showinfo if event["ok"] else messagebox.showerror)(
                    "Vérifier l’installation", event["message"], parent=self.root)
        elif event["type"] == "error":
            self._write_log(event["message"], level=logging.WARNING)
            if not self._closing:
                messagebox.showerror(event.get("title", "Import impossible"), event["message"], parent=self.root)

    def _drain(self, events: queue.Queue, limit: int | None, handler) -> None:
        """Traite au plus ``limit`` événements ; un événement fautif est journalisé."""
        count = 0
        while limit is None or count < limit:
            try:
                event = events.get_nowait()
            except queue.Empty:
                return
            count += 1
            try:
                handler(event)
            except Exception as exc:
                self._report_ui_error(exc)

    def _report_ui_error(self, exc: BaseException) -> None:
        LOGGER.error("Erreur d’affichage ignorée", exc_info=exc)
        try:
            self._write_log(f"Erreur d’affichage ignorée : {exc}", level=None)
        except Exception:
            pass

    def _poll(self) -> None:
        if self._disposed:
            return
        try:
            # Ne pas monopoliser l'interface si beaucoup d'événements arrivent ensemble.
            if self._recorder is not None:
                self._drain(self._recorder.events, 100, self._handle_event)
            self._drain(self._background_events, 30, self._handle_background_event)
            self._review.poll()
            self._update_controls()
            self._sync_floating_bar()
            self._sync_hotkeys()
        except Exception as exc:
            self._report_ui_error(exc)
        finally:
            # Toujours reprogrammer : sinon l'interface se fige et la file du
            # moteur grossit sans fin avec les aperçus des captures.
            if not self._disposed:
                delay = 100 if self._closing or self._active() or self._background_running() else 300
                self._timer.start(delay)

    # ----- Barre de menus --------------------------------------------------------

    def _build_menu(self) -> None:
        """Fichier, Enregistrement, Guide, Affichage, Aide, avec leurs raccourcis."""
        self.menubar = self.root.menuBar()
        self.menus: dict[str, QMenu] = {}
        self.actions: dict[tuple[str, str], QAction] = {}
        review = self._review

        def guide(action):
            def run():
                self._tabs.setCurrentWidget(review)
                action()
            return run

        structure = {
            "Fichier": [
                ("Ouvrir un guide…", "Ctrl+O", self._choose_session),
                ("Guides récents", None, "recent"),
                ("Importer un dossier ShareX…", "Ctrl+I", self._import),
                None,
                ("Exporter le rapport autonome", "Ctrl+E", self._export_standalone),
                ("Exporter en Markdown", None, lambda: self._export_from_guide("markdown")),
                ("Ouvrir le dossier du guide", None, self._open_session),
                None,
                ("Paramètres…", "Ctrl+,", self._open_settings),
                None,
                ("Quitter", "Ctrl+Q", self._close),
            ],
            "Enregistrement": [
                ("Démarrer l’enregistrement", "Ctrl+R", self._start),
                ("Pause / Reprendre", "Ctrl+Maj+F9", lambda: self._hotkey("pause")),
                ("Ajouter une note…", "Ctrl+Maj+F10", lambda: self._hotkey("note")),
                ("Arrêter", "Ctrl+Maj+F11", lambda: self._hotkey("stop")),
                None,
                ("Actualiser les cibles", "F5", self._refresh_targets),
                ("check", "Barre flottante pendant l’enregistrement", self.compact),
            ],
            "Guide": [
                ("Annuler", "Ctrl+Z", guide(review.undo)),
                None,
                ("Modifier le titre de l’étape", "F2", guide(review._focus_title)),
                ("Monter l’étape", "Ctrl+↑", guide(lambda: review._move(-1))),
                ("Descendre l’étape", "Ctrl+↓", guide(lambda: review._move(1))),
                ("Masquer une zone…", None, guide(review._redact)),
                ("Supprimer l’étape", "Suppr", guide(review._delete)),
            ],
            "Affichage": [
                ("Onglet Enregistrer", "Ctrl+1", lambda: self._tabs.setCurrentIndex(0)),
                ("Onglet Guide", "Ctrl+2", self._show_guide_tab),
                ("Onglet Journal", "Ctrl+3", lambda: self._tabs.setCurrentIndex(2)),
                None,
                ("check", "Réglages avancés", self._advanced_open),
                ("theme", "Thème", None),
            ],
            "Aide": [
                ("Raccourcis clavier", "F1", self._show_shortcuts),
                ("Documentation en ligne", None, lambda: self._open_url(DOCUMENTATION_URL)),
                ("Signaler un problème", None, lambda: self._open_url(ISSUES_URL)),
                None,
                ("Ouvrir le dossier du journal", None, self._open_log_folder),
                ("Ouvrir le dossier des paramètres", None, lambda: self._open_path(settings_path().parent)),
                ("Vérifier l’installation", None, self._check_installation),
                ("Rechercher des mises à jour…", None, lambda: self.check_updates(manual=True)),
                None,
                ("À propos de StepWatch", None, self._show_about),
            ],
        }
        # Raccourcis propres à la fenêtre ; ceux de l'éditeur (Suppr, Ctrl+Z…) et
        # les raccourcis d'enregistrement sont gérés ailleurs, selon le contexte.
        window_shortcuts = {"Ctrl+O", "Ctrl+I", "Ctrl+E", "Ctrl+,", "Ctrl+Q", "Ctrl+R", "F5", "Ctrl+1", "Ctrl+2",
                            "Ctrl+3", "F1"}
        for title, entries in structure.items():
            menu = self.menubar.addMenu(title)
            menu.aboutToShow.connect(self._refresh_menus)
            self.menus[title] = menu
            for entry in entries:
                if entry is None:
                    menu.addSeparator()
                elif entry[0] == "check":
                    _, label, variable = entry
                    action = QAction(label, menu)
                    action.setCheckable(True)
                    variable.bind_check(action)
                    if variable is self._advanced_open:
                        action.toggled.connect(self._show_advanced)
                    menu.addAction(action)
                    self.actions[(title, label)] = action
                elif entry[0] == "theme":
                    themes = menu.addMenu("Thème")
                    group = QActionGroup(themes)
                    self._theme_actions = {}
                    for key, label in (("systeme", "Comme le système"), ("clair", "Clair"), ("sombre", "Sombre")):
                        action = themes.addAction(label, lambda key=key: self._choose_theme(key))
                        action.setCheckable(True)
                        group.addAction(action)
                        self._theme_actions[key] = action
                    themes.aboutToShow.connect(
                        lambda: self._theme_actions[self._prefs.values["theme"]].setChecked(True))
                elif entry[2] == "recent":
                    recent = menu.addMenu(entry[0])
                    recent.aboutToShow.connect(lambda recent=recent: self._fill_recent_menu(recent))
                    self.menus["Guides récents"] = recent
                else:
                    label, accelerator, command = entry
                    action = QAction(label, menu)
                    action.triggered.connect(lambda _checked=False, command=command: self._menu_command(command))
                    if accelerator in window_shortcuts:
                        action.setShortcut(QKeySequence(accelerator))
                        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
                    elif accelerator:
                        # Affiché seulement : géré par l'éditeur ou les raccourcis d'enregistrement.
                        action.setText(f"{label}\t{accelerator}")
                    menu.addAction(action)
                    self.actions[(title, label)] = action

    def action(self, menu: str, label: str) -> QAction:
        return self.actions[(menu, label)]

    def _menu_command(self, command) -> None:
        if not self._closing:
            command()

    def _show_guide_tab(self) -> None:
        self._tabs.setCurrentWidget(self._review)
        self._show_review()

    def _refresh_menus(self) -> None:
        """États des entrées au moment où un menu s'ouvre."""
        state = self._recorder.state if self._recorder is not None else "idle"
        active = state in self.ACTIVE_STATES
        busy = self._closing or self._background_running()
        recording = state in {"recording", "paused"} and not self._closing
        review = self._review
        position = review._position()
        has_step = review._can_edit() and position is not None and position < len(review.steps)

        def set_state(menu, label, enabled):
            self.actions[(menu, label)].setEnabled(bool(enabled))

        for label in ("Ouvrir un guide…", "Importer un dossier ShareX…"):
            set_state("Fichier", label, not active and not busy)
        for label in ("Exporter le rapport autonome", "Exporter en Markdown"):
            set_state("Fichier", label, self._session_dir is not None and not active and not busy)
        set_state("Fichier", "Ouvrir le dossier du guide", self._session_dir is not None)
        set_state("Enregistrement", "Démarrer l’enregistrement",
                  not active and not busy and self._backend is not None)
        for label in ("Pause / Reprendre", "Ajouter une note…", "Arrêter"):
            set_state("Enregistrement", label, recording)
        set_state("Enregistrement", "Actualiser les cibles",
                  not active and self._backend is not None and not self._uses_portal)
        set_state("Guide", "Annuler", review._can_edit() and bool(review._undo))
        set_state("Guide", "Monter l’étape", has_step and position > 0)
        set_state("Guide", "Descendre l’étape", has_step and position < len(review.steps) - 1)
        for label in ("Modifier le titre de l’étape", "Masquer une zone…", "Supprimer l’étape"):
            set_state("Guide", label, has_step)

    # ----- Paramètres et aide --------------------------------------------------

    def _open_settings(self) -> None:
        if self._closing:
            return
        self._store_preferences()  # Les valeurs affichées reprennent l'état actuel.
        dialog = SettingsDialog(self.root, dict(self._prefs.values),
                                on_apply=self._apply_settings, on_reset=self._reset_settings)
        self._settings_dialog = dialog
        dialog.open()

    def _choose_theme(self, key: str) -> None:
        self._prefs.values["theme"] = key
        self._prefs.save()
        self.apply_theme()

    def _apply_settings(self, values: dict) -> None:
        for key in SETTINGS_KEYS:
            if key in values:
                self._prefs.values[key] = values[key]
        prefs = self._prefs.values
        self.output.set(prefs["output"])
        self.compact.set(prefs["compact"])
        if not self._active():
            self.name.set(expand_name(prefs["name_template"]))
        self._prefs.save()
        self.apply_theme()
        self._sync_tray()
        self._write_log("Paramètres enregistrés.")

    def _reset_settings(self) -> None:
        self._prefs.reset()
        self._apply_preferences(theme=True)
        self._prefs.save()
        self._sync_tray()
        self._write_log("Paramètres réinitialisés (les guides récents sont conservés).")

    def _open_url(self, url: str) -> None:
        import webbrowser

        if not webbrowser.open(url):
            self._write_log(f"Navigateur indisponible : ouvrez {url}")

    def _open_log_folder(self) -> None:
        from .diagnostics import log_directory

        self._open_path(log_directory())

    def _check_installation(self) -> None:
        """Auto-test des images, rapports et import, sans réseau ni données personnelles."""
        def work() -> None:
            import tempfile

            from .selftest import check_reports
            try:
                with tempfile.TemporaryDirectory(prefix="stepwatch-verification-") as temporary:
                    check_reports(Path(temporary))
                event = {"type": "selfcheck", "ok": True,
                         "message": "Installation vérifiée : images, masques, rapports, notes et import ShareX."}
            except Exception as exc:
                LOGGER.exception("Vérification de l’installation en échec")
                event = {"type": "selfcheck", "ok": False, "message": f"Vérification en échec : {exc}"}
            self._background_events.put(event)

        threading.Thread(target=work, name="StepWatch-verification", daemon=True).start()
        self._write_log("Vérification de l’installation en cours…")

    # ----- Mises à jour ------------------------------------------------------------

    def schedule_update_check(self, delay_ms: int = 4000) -> None:
        """Vérification automatique au démarrage : au plus une fois par jour, désactivable."""
        values = self._prefs.values
        if (self._prefs.persistent and values["check_updates"]
                and values["last_update_check"] != time.strftime("%Y-%m-%d")):
            self.after(delay_ms, lambda: self.check_updates(manual=False))

    def check_updates(self, manual: bool = False) -> None:
        if self._closing or (self._update_thread is not None and self._update_thread.is_alive()):
            return

        def work() -> None:
            try:
                event = {"type": "update", "manual": manual, "release": updater.fetch_latest()}
            except updater.UpdateError as exc:
                event = {"type": "update", "manual": manual, "error": str(exc)}
            except Exception as exc:
                LOGGER.exception("Recherche de mise à jour en échec")
                event = {"type": "update", "manual": manual, "error": f"Recherche impossible : {exc}"}
            self._background_events.put(event)

        self._update_thread = threading.Thread(target=work, name="StepWatch-version", daemon=True)
        self._update_thread.start()
        if manual:
            self._write_log("Recherche d’une nouvelle version sur GitHub…")

    def _update_checked(self, event: dict) -> None:
        from . import __version__

        manual = event["manual"]
        self._prefs.values["last_update_check"] = time.strftime("%Y-%m-%d")
        self._prefs.save()
        if "error" in event:
            self._write_log(f"Mises à jour : {event['error']}", level=logging.INFO if manual else logging.DEBUG)
            if manual and not self._closing:
                messagebox.showerror("Rechercher des mises à jour", event["error"], parent=self.root)
            return
        release: updater.Release = event["release"]
        if not updater.is_newer(release.version):
            if manual:
                self._write_log(f"StepWatch {__version__} est à jour.")
                if not self._closing:
                    messagebox.showinfo("Rechercher des mises à jour",
                                        f"StepWatch {__version__} est la dernière version publiée.", parent=self.root)
            return
        self._write_log(f"Nouvelle version disponible : StepWatch {release.version} ({release.page}).")
        if not manual and release.version == self._prefs.values["skipped_version"]:
            return
        if self._active():
            self._pending_release = release  # Ne pas interrompre l'intervention.
            return
        self._show_update(release)

    def _show_update(self, release: updater.Release):
        if self._closing:
            return None
        dialog = self._update_dialog
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            return dialog
        from .update_dialog import UpdateDialog

        self._update_dialog = UpdateDialog(self.root, release, updater.detect_installation(),
                                           blocked=self._update_blocked, on_skip=self._skip_version,
                                           on_installed=self._update_installed, open_url=self._open_url)
        self._update_dialog.show()
        return self._update_dialog

    def _update_blocked(self) -> str | None:
        if self._active():
            return "Arrêtez d’abord l’enregistrement en cours : la mise à jour redémarre StepWatch."
        if self._background_running():
            return "Un rapport est en cours de création. Réessayez dans quelques secondes."
        return None

    def _skip_version(self, version: str) -> None:
        self._prefs.values["skipped_version"] = version
        self._prefs.save()
        self._write_log(f"Version {version} ignorée : elle ne sera plus proposée au démarrage.")

    def _update_installed(self, command: list[str] | None) -> None:
        self._write_log("Mise à jour installée. Redémarrage de StepWatch…")
        if command:
            try:
                updater.relaunch(command)
            except OSError as exc:
                self._write_log(f"Redémarrage automatique impossible ({exc}) : relancez StepWatch.")
                messagebox.showinfo("Mise à jour de StepWatch",
                                    "Mise à jour installée. Relancez StepWatch pour utiliser la nouvelle version.",
                                    parent=self.root)
                return
        self._close()

    def _show_shortcuts(self) -> None:
        lines = [
            ("Ctrl+R", "Démarrer l’enregistrement"),
            ("Ctrl+Maj+F9", "Pause / Reprendre" + (" (dans toutes les applications)"
                                                   if sys.platform == "win32" else "")),
            ("Ctrl+Maj+F10", "Ajouter une note"),
            ("Ctrl+Maj+F11", "Arrêter l’enregistrement"),
            ("Ctrl+O / Ctrl+I", "Ouvrir un guide / Importer ShareX"),
            ("Ctrl+E", "Exporter le rapport autonome"),
            ("Ctrl+1, 2, 3", "Onglets Enregistrer, Guide, Journal"),
            ("Suppr, Ctrl+↑/↓", "Supprimer, déplacer l’étape (onglet Guide)"),
            ("Ctrl+Z, F2", "Annuler, modifier le titre de l’étape"),
            ("F5", "Actualiser les cibles"),
            ("Ctrl+,", "Paramètres"),
            ("Ctrl+Q", "Quitter"),
        ]
        messagebox.showinfo("Raccourcis clavier", "\n".join(f"{keys}  —  {text}" for keys, text in lines),
                            parent=self.root)

    def _show_about(self) -> None:
        from PySide6 import __version__ as pyside_version

        from . import __version__

        messagebox.showinfo(
            "À propos de StepWatch",
            f"StepWatch {__version__}\n\nDocumentation locale des interventions, étape par étape.\n"
            "Captures et rapports restent sur ce poste : aucun compte, aucune télémétrie.\n\n"
            f"Python {sys.version.split()[0]} · Qt {qVersion()} · PySide6 {pyside_version}\n"
            "Licence GNU AGPL v3 · Qt et PySide6 sous licence LGPL v3",
            parent=self.root)

    # ----- Zone de notification -------------------------------------------------

    def _sync_tray(self) -> None:
        wanted = self._prefs.values["tray_icon"] and QSystemTrayIcon.isSystemTrayAvailable()
        if wanted and self._tray is None:
            tray = QSystemTrayIcon(qicon())
            menu = QMenu()
            self._tray_actions = {
                "show": menu.addAction("Afficher StepWatch", self._show_window),
                "start": menu.addAction("Démarrer l’enregistrement", self._start),
                "pause": menu.addAction("Pause / Reprendre", lambda: self._hotkey("pause")),
                "note": menu.addAction("Ajouter une note…", lambda: self._hotkey("note")),
                "stop": menu.addAction("Arrêter", lambda: self._hotkey("stop")),
            }
            menu.addSeparator()
            self._tray_actions["quit"] = menu.addAction("Quitter", self._close)
            tray.setContextMenu(menu)
            tray.activated.connect(lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger
                                   and self._show_window())
            self._tray_menu = menu
            self._tray = tray
            tray.show()
            self._controls_key = None
        elif not wanted and self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None

    def _update_tray(self, state: str) -> None:
        if self._tray is None:
            return
        recording = state in {"recording", "paused"} and not self._closing
        actions = self._tray_actions
        actions["start"].setEnabled(state not in self.ACTIVE_STATES and self._backend is not None
                                    and not self._closing and not self._background_running())
        for name in ("pause", "note", "stop"):
            actions[name].setEnabled(recording)
        self._tray.setToolTip(f"StepWatch · {self.state_text.get()}")

    def _show_window(self) -> None:
        self._withdrawn = False
        self.root.showNormal()
        self.root.raise_()
        self.root.activateWindow()

    # ----- Barre flottante pendant l'enregistrement ------------------------------

    def _sync_floating_bar(self) -> None:
        recorder = self._recorder
        state = recorder.state if recorder is not None else "idle"
        if state in {"recording", "paused"} and self._compact_session and self._bar is None and not self._closing:
            self._bar = FloatingBar(icon=self._icon_pixmap(20), on_pause=self._pause, on_resume=self._resume,
                                    on_note=self._add_note, on_stop=self._stop, on_expand=self._expand_from_bar)
            self._bar.show_at(self._prefs.values["bar_position"])
            self._bar_excluded = exclude_from_capture(self._window_handle(self._bar))
            self.root.hide()
            self._withdrawn = True
            self._regions_key = None
        if self._bar is not None:
            if state in self.ACTIVE_STATES and not self._closing:
                self._bar.update_state(state, self.kept.get())
            else:
                self._hide_floating_bar()
        if recorder is not None and state in self.ACTIVE_STATES:
            self._send_regions(recorder)

    # ----- Raccourcis pendant l'enregistrement --------------------------------

    def _sync_hotkeys(self) -> None:
        state = self._recorder.state if self._recorder is not None else "idle"
        hotkeys = self._hotkeys
        if hotkeys is None:
            return
        wanted = (state in {"recording", "paused"} and not self._closing
                  and self._prefs.values["global_hotkeys"])
        if wanted and not self._hotkeys_on:
            self._hotkeys_on = True
            if hotkeys.start():
                labels = ", ".join(f"{HOTKEY_LABELS[name]} {text}" for name, text in
                                   (("pause", "pause/reprise"), ("note", "note"), ("stop", "arrêt")))
                self._write_log(f"Raccourcis actifs dans toutes les applications : {labels}.")
                for label in hotkeys.failed:
                    self._write_log(f"Raccourci {label} déjà utilisé par une autre application : indisponible.")
        elif not wanted and self._hotkeys_on:
            self._hotkeys_on = False
            hotkeys.stop()
        while True:
            try:
                action = hotkeys.events.get_nowait()
            except queue.Empty:
                break
            self._hotkey(action)

    def _hotkey(self, action: str) -> None:
        state = self._recorder.state if self._recorder is not None else "idle"
        if state not in {"recording", "paused"} or self._closing:
            return
        if action == "pause":
            self._resume() if state == "paused" else self._pause()
        elif action == "note":
            self._add_note()
        elif action == "stop":
            self._stop()

    def _hide_floating_bar(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar.deleteLater()
            self._bar = None
        self._compact_session = False
        if self._withdrawn and not self._disposed:
            self._show_window()

    def _expand_from_bar(self) -> None:
        """Rouvre la fenêtre principale ; la barre reste disponible."""
        if self._withdrawn:
            self._show_window()

    @staticmethod
    def _window_handle(window: QWidget) -> int:
        if sys.platform != "win32":
            return 0
        try:
            return int(window.winId())
        except (RuntimeError, ValueError, TypeError):
            return 0

    @staticmethod
    def _native_rect(widget: QWidget) -> Rect | None:
        """Rectangle d'une fenêtre (cadre compris) en pixels physiques de l'écran."""
        frame = widget.frameGeometry()
        if frame.width() <= 1 or frame.height() <= 1:
            return None
        screen = widget.screen() or QGuiApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen is not None else 1.0
        origin = screen.geometry().topLeft() if screen is not None else frame.topLeft()
        # Qt garde l'origine de chaque écran en pixels natifs et met à l'échelle le reste.
        left = origin.x() + round((frame.x() - origin.x()) * ratio)
        top = origin.y() + round((frame.y() - origin.y()) * ratio)
        return Rect(left, top, round(frame.width() * ratio), round(frame.height() * ratio))

    def _own_regions(self) -> tuple[list[Rect], list[Rect]]:
        """Rectangles écran de StepWatch : clics ignorés, et parties à masquer."""
        regions, masked = [], []
        windows = []
        if self._bar is not None and self._bar.isVisible():
            windows.append((self._bar, not self._bar_excluded))
        if not self._withdrawn and self.root.isVisible() and not self.root.isMinimized():
            windows.append((self.root, True))
        for window, mask in windows:
            rect = self._native_rect(window)
            if rect is None:
                continue
            regions.append(rect)
            if mask and not self._uses_portal:
                masked.append(rect)
        return regions, masked

    def _send_regions(self, recorder: Recorder) -> None:
        setter = getattr(recorder, "set_excluded_regions", None)
        if setter is None:
            return
        regions, masked = self._own_regions()
        key = tuple(regions), tuple(masked)
        if key != self._regions_key:
            self._regions_key = key
            setter(regions, masked)

    # ----- Import, export, dossiers ---------------------------------------------

    def _import(self) -> None:
        if self._active() or self._closing or self._background_running():
            return
        source = filedialog.askdirectory(parent=self.root, title="Dossier contenant les captures ShareX")
        if not source:
            return
        try:
            output = self._output_dir()
        except ValueError as exc:
            messagebox.showerror("Dossier requis", str(exc), parent=self.root)
            return
        name = self.name.get().strip() or "Import ShareX"
        events = self._background_events

        def work() -> None:
            try:
                directory = import_sharex(Path(source), output, name=name)
                events.put({"type": "import_done", "directory": str(directory)})
            except Exception as exc:
                events.put({"type": "error", "message": f"Import interrompu : {exc}"})

        self._import_thread = threading.Thread(target=work, name="stepwatch-import", daemon=False)
        self._import_thread.start()
        self._write_log("Import ShareX : copie et classement des images, sans modification des sources.")
        self._update_controls()

    def _export_standalone(self) -> None:
        if self._session_dir is None or self._active() or self._closing or self._background_running():
            return
        directory = self._session_dir
        recorder = self._recorder
        # Un dernier enregistrement du rapport a pu échouer après l'écriture
        # des PNG : réessayer avec les étapes en mémoire de cette session
        # plutôt que d'exporter un rapport apparemment complet mais périmé.
        store = (recorder.store if recorder is not None
                 and recorder.store.directory == directory else None)
        events = self._background_events

        def work() -> None:
            try:
                if store is not None:
                    store.close()
                path = export_standalone(directory)
                markdown = export_markdown(directory)
                events.put({"type": "export_done", "path": str(path),
                            "size": path.stat().st_size, "markdown": str(markdown)})
            except Exception as exc:
                events.put({"type": "error", "title": "Export impossible",
                            "message": f"Export du rapport autonome interrompu : {exc}"})

        self._export_thread = threading.Thread(target=work, name="stepwatch-export", daemon=False)
        self._export_thread.start()
        self._write_log("Export du rapport autonome : intégration des captures dans un seul fichier HTML…")
        self._update_controls()

    def _open_session(self) -> None:
        if self._session_dir is not None:
            self._open_path(self._session_dir)

    def _open_path(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True) if not path.exists() else None
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            self._write_log(f"Ouverture du dossier impossible : {exc}")

    # ----- Arrêt -----------------------------------------------------------------

    def shutdown(self, timeout: float = 5) -> bool:
        """Arrête les traitements avant de libérer Qt, sans les forcer."""
        deadline = time.monotonic() + max(0, timeout)
        complete = True
        if self._recorder is not None:
            try:
                stopped = self._recorder.stop(timeout=max(0, timeout))
            except Exception as exc:
                LOGGER.exception("Arrêt de l’enregistrement impossible à la fermeture")
                try:
                    self._write_log(f"Arrêt incomplet : {exc}. Le processus peut rester actif.", level=None)
                except Exception:
                    pass
                complete = False
            else:
                if not stopped:
                    message = (f"Arrêt incomplet après {timeout:g} s : le thread de capture est encore actif. "
                               "Le processus peut rester ouvert ; aucune fermeture forcée n’est effectuée.")
                    LOGGER.warning(message)
                    try:
                        self._write_log(message, level=None)
                    except Exception:
                        pass
                    complete = False
        for worker in (self._import_thread, self._export_thread):
            if worker is not None and worker is not threading.current_thread() and worker.is_alive():
                worker.join(timeout=max(0, deadline - time.monotonic()))
                if worker.is_alive():
                    complete = False
                    LOGGER.warning("Le traitement %s est encore actif ; l’interface est conservée.", worker.name)
        if not self._review.shutdown(max(0, deadline - time.monotonic())):
            complete = False
            LOGGER.warning("La lecture de la capture est encore active.")
        return complete

    def dispose(self) -> None:
        """Libère l'interface sur son thread, après l'arrêt des traitements."""
        if self._disposed:
            return
        if threading.get_ident() != self._ui_thread:
            raise RuntimeError("L’interface doit être libérée sur son thread.")
        if self._hotkeys is not None:
            self._hotkeys.stop()
        if self._bar is not None:
            self._bar.close()
            self._bar.deleteLater()
            self._bar = None
        if not self._review.shutdown():
            raise RuntimeError("La lecture de la capture est encore active.")
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:
                LOGGER.exception("Fermeture des ressources de sélection impossible")
        self._timer.stop()
        self._disposed = True
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None
        for dialog in (self._settings_dialog, self._update_dialog):
            if dialog is not None:
                try:
                    dialog.close()
                except RuntimeError:
                    pass
        self.root.allow_close = True
        self.root.close()
        self.root.deleteLater()
        self._last_image = None
        if self.quit_on_dispose:
            self.qapp.quit()

    def _close(self) -> None:
        if self._closing or self._disposed:
            return
        if self._active() and not messagebox.askyesno(
                "Fermer StepWatch", "Arrêter l’intervention et finaliser le rapport avant de fermer ?",
                parent=self.root):
            return
        self._closing = True
        try:
            self._store_preferences()
        except Exception:
            LOGGER.exception("Préférences non enregistrées à la fermeture")
        self._finish_close()

    def _finish_close(self) -> None:
        retry = True
        try:
            recorder_done = self._recorder is None or self._recorder.stop(timeout=0)
            self._update_controls()
            background_done = not self._background_running()
            if not recorder_done or not background_done:
                return
            self.dispose()
            retry = False
        except Exception as exc:
            # Une erreur passagère ne doit pas bloquer la fermeture : nouvelle tentative.
            message = str(exc)
            if message != self._close_error:
                self._close_error = message
                LOGGER.exception("Fermeture incomplète ; nouvelle tentative")
                try:
                    self._write_log(f"Fermeture incomplète : {exc}. Nouvelle tentative…", level=None)
                except Exception:
                    pass
        finally:
            if retry and not self._disposed:
                QTimer.singleShot(100, self._finish_close)


def _log_unhandled(kind, value, traceback) -> None:
    """Sans console (exécutable Windows), une erreur d'un rappel Qt serait perdue."""
    LOGGER.error("Erreur dans un rappel de l’interface", exc_info=(kind, value, traceback))


def run_app(log_path: Path | None = None) -> None:
    qapp = application()
    previous_hook = sys.excepthook
    sys.excepthook = _log_unhandled
    app = None
    try:
        app = StepWatchApp(log_path, preferences=Preferences())
        app.quit_on_dispose = True
        app.root.show()
        app.schedule_update_check()
        qapp.exec()
    finally:
        sys.excepthook = previous_hook
        # Ctrl+C ou exception sortie de la boucle Qt : sans cet arrêt, le
        # thread de capture continuait d'enregistrer sans interface.
        if app is not None and not app._disposed:
            if app.shutdown():
                app.quit_on_dispose = False
                app.dispose()
