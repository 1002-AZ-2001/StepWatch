"""Fenêtre « Mise à jour disponible » : notes de version, téléchargement vérifié, installation."""

from __future__ import annotations

import html
import logging
import queue
import re
import threading
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from . import __version__, updater
from .qtcompat import messagebox
from .theme import set_role

LOGGER = logging.getLogger(__name__)


def format_notes(markdown: str) -> list[tuple[str, str]]:
    """Notes de version Markdown en lignes (texte, style) : titres, puces, tableaux lisibles."""
    lines: list[tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if re.fullmatch(r"\|?[\s:|-]+\|?", line) and "-" in line:
            continue  # Ligne de séparation d'un tableau.
        style = "text"
        heading = re.match(r"#{1,6}\s+(.*)", line)
        if heading:
            line, style = heading.group(1), "heading"
        elif line.startswith("|"):
            line = " — ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
        elif re.match(r"[-*]\s+", line):
            line = "• " + re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # Liens : garder le texte.
        line = line.replace("**", "").replace("`", "")
        if line or (lines and lines[-1][0]):
            lines.append((line, style))
    return lines


def notes_html(markdown: str) -> str:
    parts = []
    for text, style in format_notes(markdown):
        if style == "heading":
            parts.append(f"<h3 style='margin:10px 0 4px 0'>{html.escape(text)}</h3>")
        elif text:
            parts.append(f"<div style='margin:2px 0'>{html.escape(text)}</div>")
        else:
            parts.append("<div style='height:6px'></div>")
    return "".join(parts)


class UpdateDialog(QDialog):
    """Propose ``release`` ; ``on_installed`` reçoit la commande de redémarrage (ou None)."""

    def __init__(self, parent: QWidget, release: updater.Release, installation: updater.Installation, *,
                 blocked: Callable[[], str | None], on_skip: Callable[[str], None],
                 on_installed: Callable[[list[str] | None], None], open_url: Callable[[str], None]):
        super().__init__(parent)
        self.release, self.installation = release, installation
        self._blocked, self._on_skip, self._on_installed, self._open_url = blocked, on_skip, on_installed, open_url
        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._installing = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)
        self.setWindowTitle("Mise à jour de StepWatch")
        self.setMinimumSize(560, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(set_role(QLabel(f"StepWatch {release.version} est disponible"), "cardtitle"))
        layout.addWidget(set_role(QLabel(f"Version installée : {__version__} · {installation.label}"), "muted"))
        self.notes = QTextBrowser()
        self.notes.setOpenLinks(False)
        self.notes.setHtml(notes_html(release.notes or "Aucune note de version."))
        layout.addWidget(self.notes, 1)
        if installation.installable:
            explanation = ("Le fichier est téléchargé depuis GitHub et vérifié (sommes SHA-256) avant "
                           "l’installation. StepWatch redémarre ensuite.")
            if installation.kind in ("deb", "arch"):
                explanation += " Le mot de passe administrateur est demandé pour installer le paquet."
        else:
            explanation = ("StepWatch est lancé depuis les sources : mettez à jour avec git ou pip, "
                           "ou téléchargez la version depuis GitHub.")
        info = set_role(QLabel(explanation), "muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status_label = set_role(QLabel(""), "muted")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton("Ignorer cette version")
        self.skip_button.clicked.connect(self._skip)
        self.page_button = QPushButton("Voir sur GitHub")
        self.page_button.clicked.connect(lambda: self._open_url(release.page))
        buttons.addWidget(self.skip_button)
        buttons.addWidget(self.page_button)
        buttons.addStretch(1)
        self.later_button = QPushButton("Plus tard")
        self.later_button.clicked.connect(self.close)
        buttons.addWidget(self.later_button)
        if installation.installable:
            self.install_button = set_role(QPushButton("Installer et redémarrer"), "primary")
            self.install_button.clicked.connect(self.install)
        else:
            self.install_button = set_role(QPushButton("Ouvrir la page de téléchargement"), "primary")
            self.install_button.clicked.connect(lambda: self._open_url(release.page))
        self.install_button.setDefault(True)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    # ----- Actions ---------------------------------------------------------------

    def status(self) -> str:
        return self.status_label.text()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.show()

    def _skip(self) -> None:
        self._on_skip(self.release.version)
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._installing:
            event.ignore()  # Paquet ou fichier en cours de remplacement : ne pas interrompre.
            return
        self._cancel.set()
        self._timer.stop()
        super().closeEvent(event)

    def reject(self) -> None:
        if not self._installing:
            self.close()

    def install(self) -> None:
        if self._worker is not None:
            return
        reason = self._blocked()
        if reason:
            messagebox.showinfo("Mise à jour de StepWatch", reason, parent=self)
            return
        asset = updater.asset_for(self.release, self.installation)
        if asset is None:
            messagebox.showerror("Mise à jour de StepWatch",
                                 f"Cette version ne contient pas de fichier pour : {self.installation.label}.",
                                 parent=self)
            return
        self.install_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self._set_status(f"Téléchargement de {asset.name}…")
        self._worker = threading.Thread(target=self._work, args=(asset,), name="StepWatch-mise-a-jour", daemon=True)
        self._worker.start()
        self._timer.start()

    def _work(self, asset: updater.Asset) -> None:
        try:
            downloaded = updater.download(
                self.release, asset, updater.download_directory(),
                progress=lambda done, total: self._events.put(("progress", done, total)),
                cancelled=self._cancel.is_set)
            self._events.put(("installing",))
            command = updater.apply(self.installation, downloaded)
            self._events.put(("done", command))
        except updater.UpdateError as exc:
            self._events.put(("error", str(exc)))
        except Exception as exc:  # Erreur inattendue : la version installée reste utilisable.
            LOGGER.exception("Mise à jour en échec")
            self._events.put(("error", f"Mise à jour en échec : {exc}"))

    def _poll(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            kind = event[0]
            if kind == "progress":
                done, total = event[1], event[2]
                if total:
                    self.progress.setValue(int(min(100, done * 100 / total)))
                    self._set_status(f"Téléchargement : {done / 1e6:.1f} / {total / 1e6:.1f} Mo")
            elif kind == "installing":
                self._installing = True
                self.later_button.setEnabled(False)
                self.progress.setValue(100)
                self._set_status("Fichier vérifié (SHA-256). Installation…")
            elif kind == "done":
                self._installing = False
                self._timer.stop()
                self._set_status("Mise à jour installée. Redémarrage de StepWatch…")
                self._on_installed(event[1])
                self.close()
                return
            elif kind == "error":
                self._installing = False
                self._timer.stop()
                self._worker = None
                self._set_status(event[1])
                for button in (self.later_button, self.skip_button, self.install_button):
                    button.setEnabled(True)
                LOGGER.warning("Mise à jour : %s", event[1])
                messagebox.showerror("Mise à jour de StepWatch", event[1], parent=self)
                return
