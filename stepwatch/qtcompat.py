"""Petits outils Qt partagés par l'interface.

- ``Value`` : valeur observable liée à un ou plusieurs widgets (texte, case,
  liste), avec ``get()`` / ``set()`` comme les variables Tk d'avant ;
- ``messagebox``, ``filedialog``, ``simpledialog`` : boîtes de dialogue
  standard en français, remplaçables dans les tests ;
- ``to_pixmap`` : image Pillow vers ``QPixmap`` ;
- ``application()`` : l'unique ``QApplication`` du processus.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QWidget)
from PySide6.QtGui import QAction

APP_ID = "stepwatch"


def choose_platform(environ=None, platform: str | None = None) -> str | None:
    """Plugin Qt à utiliser sous Linux, ou None pour le choix de Qt.

    Sous Wayland, la barre flottante doit rester au premier plan et à une
    position choisie : Wayland ne le permet pas à une application ordinaire.
    StepWatch passe donc par XWayland quand il est disponible, et s'affiche
    en Wayland natif sinon. ``STEPWATCH_WAYLAND=natif`` force le Wayland natif ;
    ``QT_QPA_PLATFORM`` reste prioritaire.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux") or environ.get("QT_QPA_PLATFORM"):
        return None
    if environ.get("WAYLAND_DISPLAY"):
        if environ.get("STEPWATCH_WAYLAND", "").lower() in {"natif", "native", "1"} or not environ.get("DISPLAY"):
            return "wayland"
        return "xcb"
    return None


def application(argv: list[str] | None = None) -> QApplication:
    """Crée (une seule fois) l'application Qt."""
    app = QApplication.instance()
    if app is None:
        chosen = choose_platform()
        if chosen:
            os.environ["QT_QPA_PLATFORM"] = chosen
        app = QApplication(argv if argv is not None else [sys.argv[0] if sys.argv else APP_ID])
        app.setApplicationName(APP_ID)
        app.setOrganizationName("StepWatch")
        # Identifiant Wayland (app_id) et nom du fichier .desktop.
        app.setDesktopFileName(APP_ID)
        # Pendant un enregistrement, seule la barre flottante (fenêtre outil) est visible :
        # fermer la fenêtre « Ajouter une note » ferait quitter Qt. StepWatch quitte
        # uniquement par sa propre fermeture (arrêt de l'enregistrement, rapports finalisés).
        app.setQuitOnLastWindowClosed(False)
        _install_french_translation(app)
    return app


def _install_french_translation(app: QApplication) -> None:
    """Boutons et dialogues standard de Qt en français (Annuler, Ouvrir…)."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    translator = QTranslator(app)
    if translator.load(QLocale(QLocale.Language.French), "qtbase", "_",
                       QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
        app._stepwatch_translator = translator  # Garder une référence Python.


def process_events(times: int = 1) -> None:
    app = QApplication.instance()
    if app is not None:
        for _ in range(times):
            app.processEvents()


# ----- Valeurs observables ------------------------------------------------------


class Value:
    """Valeur partagée entre le code et des widgets, synchronisée dans les deux sens."""

    def __init__(self, value: Any = ""):
        self._value = value
        self._listeners: list[Callable[[Any], None]] = []
        self._updating = False

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        if value == self._value:
            return
        self._value = value
        self._updating = True
        try:
            for listener in list(self._listeners):
                listener(value)
        finally:
            self._updating = False

    def trace(self, listener: Callable[[Any], None]) -> None:
        self._listeners.append(listener)

    def _from_widget(self, value: Any) -> None:
        if not self._updating:
            self.set(value)

    # Liaisons avec les widgets ---------------------------------------------------

    def bind_line(self, widget: QLineEdit) -> QLineEdit:
        widget.setText(str(self._value))
        widget.textChanged.connect(self._from_widget)
        self.trace(lambda value: widget.text() != str(value) and widget.setText(str(value)))
        return widget

    def bind_label(self, widget: QLabel) -> QLabel:
        widget.setText(str(self._value))
        self.trace(lambda value: widget.setText(str(value)))
        return widget

    def bind_check(self, widget: QCheckBox | QAction) -> QCheckBox | QAction:
        widget.setChecked(bool(self._value))
        signal = widget.toggled
        signal.connect(lambda checked: self._from_widget(bool(checked)))
        self.trace(lambda value: widget.isChecked() != bool(value) and widget.setChecked(bool(value)))
        return widget

    def bind_combo(self, widget: QComboBox) -> QComboBox:
        """Texte affiché du choix ; une valeur absente de la liste est affichée telle quelle."""
        def show(value: Any) -> None:
            index = widget.findText(str(value))
            if index >= 0:
                if widget.currentIndex() != index:
                    widget.setCurrentIndex(index)
            elif widget.isEditable():
                widget.setEditText(str(value))
            else:
                widget.setCurrentIndex(-1)
                widget.setPlaceholderText(str(value))
        show(self._value)
        widget.currentTextChanged.connect(lambda text: text and self._from_widget(text))
        self.trace(show)
        return widget


# ----- Boîtes de dialogue -------------------------------------------------------


def _parent(parent: QWidget | None) -> QWidget | None:
    return parent if isinstance(parent, QWidget) else QApplication.activeWindow()


def _french_buttons(box: QMessageBox) -> None:
    for button, text in ((QMessageBox.StandardButton.Yes, "Oui"), (QMessageBox.StandardButton.No, "Non"),
                         (QMessageBox.StandardButton.Ok, "OK"), (QMessageBox.StandardButton.Cancel, "Annuler")):
        widget = box.button(button)
        if widget is not None:
            widget.setText(text)


class messagebox:  # noqa: N801 - même usage que tkinter.messagebox
    @staticmethod
    def _show(icon, title: str, message: str, parent=None, buttons=QMessageBox.StandardButton.Ok,
              default=QMessageBox.StandardButton.Ok) -> QMessageBox.StandardButton:
        box = QMessageBox(icon, title, message, buttons, _parent(parent))
        box.setDefaultButton(default)
        _french_buttons(box)
        return box.exec()

    @staticmethod
    def showinfo(title: str, message: str, parent=None) -> None:
        messagebox._show(QMessageBox.Icon.Information, title, message, parent)

    @staticmethod
    def showwarning(title: str, message: str, parent=None) -> None:
        messagebox._show(QMessageBox.Icon.Warning, title, message, parent)

    @staticmethod
    def showerror(title: str, message: str, parent=None) -> None:
        messagebox._show(QMessageBox.Icon.Critical, title, message, parent)

    @staticmethod
    def askyesno(title: str, message: str, parent=None) -> bool:
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        answer = messagebox._show(QMessageBox.Icon.Question, title, message, parent, buttons,
                                  QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes


class filedialog:  # noqa: N801
    @staticmethod
    def askdirectory(parent=None, title: str = "", initialdir: str | None = None) -> str:
        return QFileDialog.getExistingDirectory(_parent(parent), title, initialdir or "")


class simpledialog:  # noqa: N801
    @staticmethod
    def askstring(title: str, prompt: str, parent=None, password: bool = False) -> str | None:
        dialog = QInputDialog(_parent(parent))
        dialog.setWindowTitle(title)
        dialog.setLabelText(prompt)
        if password:
            dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
        dialog.setOkButtonText("OK")
        dialog.setCancelButtonText("Annuler")
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        return dialog.textValue() if dialog.exec() else None


# ----- Images -----------------------------------------------------------------------


def to_qimage(image: Image.Image) -> QImage:
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    return QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def to_pixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(to_qimage(image))
