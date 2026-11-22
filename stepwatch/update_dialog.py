"""Fenêtre « Mise à jour disponible » : notes de version, téléchargement vérifié, installation."""

from __future__ import annotations

import logging
import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable

from . import __version__, updater
from .theme import PALETTE

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


class UpdateDialog(tk.Toplevel):
    """Propose ``release`` ; ``on_installed`` reçoit la commande de redémarrage (ou None)."""

    def __init__(self, parent: tk.Misc, release: updater.Release, installation: updater.Installation, *,
                 font: str, blocked: Callable[[], str | None], on_skip: Callable[[str], None],
                 on_installed: Callable[[list[str] | None], None], open_url: Callable[[str], None]):
        super().__init__(parent)
        p = PALETTE
        self.release, self.installation = release, installation
        self._blocked, self._on_skip, self._on_installed, self._open_url = blocked, on_skip, on_installed, open_url
        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._installing = False
        self._poll_id: str | None = None
        self.title("Mise à jour de StepWatch")
        self.configure(bg=p["bg"])
        self.transient(parent.winfo_toplevel())
        self.minsize(520, 360)
        self.protocol("WM_DELETE_WINDOW", self._close)

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text=f"StepWatch {release.version} est disponible",
                  font=(font, 14, "bold")).grid(sticky="w")
        ttk.Label(frame, text=f"Version installée : {__version__} · {installation.label}",
                  style="Muted.TLabel").grid(sticky="w", pady=(2, 10))
        notes = scrolledtext.ScrolledText(frame, height=12, width=72, wrap="word", relief="flat",
                                          font=(font, 10), bg=p["surface"], fg=p["text"],
                                          highlightthickness=1, highlightbackground=p["border"])
        notes.tag_configure("heading", font=(font, 11, "bold"), spacing1=6, spacing3=2)
        for text, style in format_notes(release.notes or "Aucune note de version."):
            notes.insert("end", text + "\n", style)
        notes.configure(state="disabled")
        notes.grid(sticky="nsew")
        self.notes = notes
        if installation.installable:
            explanation = ("Le fichier est téléchargé depuis GitHub et vérifié (sommes SHA-256) avant "
                           "l’installation. StepWatch redémarre ensuite.")
            if installation.kind in ("deb", "arch"):
                explanation += " Le mot de passe administrateur est demandé pour installer le paquet."
        else:
            explanation = ("StepWatch est lancé depuis les sources : mettez à jour avec git ou pip, "
                           "ou téléchargez la version depuis GitHub.")
        ttk.Label(frame, text=explanation, style="Muted.TLabel", wraplength=560,
                  justify="left").grid(sticky="w", pady=(10, 6))
        self.status = tk.StringVar(master=self, value="")
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.status_label = ttk.Label(frame, textvariable=self.status, style="Muted.TLabel")

        buttons = ttk.Frame(frame)
        buttons.grid(sticky="ew", pady=(10, 0), row=6)
        self.skip_button = ttk.Button(buttons, text="Ignorer cette version", command=self._skip)
        self.skip_button.pack(side="left")
        self.page_button = ttk.Button(buttons, text="Voir sur GitHub", command=lambda: self._open_url(release.page))
        self.page_button.pack(side="left", padx=(8, 0))
        if installation.installable:
            self.install_button = ttk.Button(buttons, text="Installer et redémarrer", style="Primary.TButton",
                                             command=self.install)
        else:
            self.install_button = ttk.Button(buttons, text="Ouvrir la page de téléchargement",
                                             style="Primary.TButton", command=lambda: self._open_url(release.page))
        self.install_button.pack(side="right")
        self.later_button = ttk.Button(buttons, text="Plus tard", command=self._close)
        self.later_button.pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self._close())
        self.install_button.focus_set()

    # ----- Actions ---------------------------------------------------------------

    def _skip(self) -> None:
        self._on_skip(self.release.version)
        self._close()

    def _close(self) -> None:
        if self._installing:
            return  # Paquet ou fichier en cours de remplacement : ne pas interrompre.
        self._cancel.set()
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self.destroy()

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
        for button in (self.install_button, self.skip_button):
            button.state(["disabled"])
        self.progress.grid(sticky="ew", row=4, pady=(4, 2))
        self.status_label.grid(sticky="w", row=5)
        self.status.set(f"Téléchargement de {asset.name}…")
        self._worker = threading.Thread(target=self._work, args=(asset,), name="StepWatch-mise-a-jour", daemon=True)
        self._worker.start()
        self._poll()

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
        self._poll_id = None
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "progress":
                done, total = event[1], event[2]
                if total:
                    self.progress.configure(value=min(100, done * 100 / total))
                    self.status.set(f"Téléchargement : {done / 1e6:.1f} / {total / 1e6:.1f} Mo")
            elif kind == "installing":
                self._installing = True
                self.later_button.state(["disabled"])
                self.progress.configure(value=100)
                self.status.set("Fichier vérifié (SHA-256). Installation…")
            elif kind == "done":
                self._installing = False
                self.status.set("Mise à jour installée. Redémarrage de StepWatch…")
                self._on_installed(event[1])
                self._close()
                return
            elif kind == "error":
                self._installing = False
                self._worker = None
                self.status.set(event[1])
                self.later_button.state(["!disabled"])
                self.skip_button.state(["!disabled"])
                self.install_button.state(["!disabled"])
                LOGGER.warning("Mise à jour : %s", event[1])
                messagebox.showerror("Mise à jour de StepWatch", event[1], parent=self)
                return
        if self.winfo_exists():
            self._poll_id = self.after(100, self._poll)
