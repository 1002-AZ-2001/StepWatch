"""Fenêtre « Paramètres » : préférences de StepWatch regroupées par thème."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable

from .settings import CHOICES, expand_name
from .theme import PALETTE

BAR_POSITIONS = {"bas": "En bas de l’écran", "haut": "En haut de l’écran"}


class SettingsDialog(tk.Toplevel):
    """Edit a copy of the preferences; ``on_apply`` receives the new values."""

    def __init__(self, parent: tk.Misc, values: dict[str, Any], *, font: str,
                 on_apply: Callable[[dict[str, Any]], None], on_reset: Callable[[], None]):
        super().__init__(parent)
        p = PALETTE
        self.title("Paramètres de StepWatch")
        self.configure(bg=p["bg"])
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self._on_apply, self._on_reset = on_apply, on_reset
        self.vars: dict[str, tk.Variable] = {}
        for key, value in values.items():
            if isinstance(value, bool):
                self.vars[key] = tk.BooleanVar(master=self, value=value)
            elif isinstance(value, (str, int)):
                self.vars[key] = tk.StringVar(master=self, value=str(value))
        self.vars["bar_position"].set(BAR_POSITIONS.get(values["bar_position"], BAR_POSITIONS["bas"]))
        self.vars["font_scale"].set(f"{values['font_scale']} %")
        self._initial_scale = values["font_scale"]

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        tabs = ttk.Notebook(frame)
        tabs.pack(fill="both", expand=True)
        self.tabs = tabs

        general = self._page(tabs, "Général")
        ttk.Label(general, text="Nom proposé pour un nouveau guide", style="Card.TLabel").grid(sticky="w")
        name = ttk.Entry(general, textvariable=self.vars["name_template"], width=46)
        name.grid(sticky="ew", pady=(4, 2))
        self.preview = ttk.Label(general, style="CardMuted.TLabel")
        self.preview.grid(sticky="w", pady=(0, 12))
        self.vars["name_template"].trace_add("write", lambda *_: self._update_preview())
        self._update_preview()
        ttk.Label(general, text="Dossier des guides", style="Card.TLabel").grid(sticky="w")
        folder = ttk.Frame(general, style="Card.TFrame")
        folder.grid(sticky="ew", pady=(4, 0))
        folder.columnconfigure(0, weight=1)
        ttk.Entry(folder, textvariable=self.vars["output"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(folder, text="Parcourir…", command=self._choose_folder).grid(row=0, column=1, padx=(8, 0))

        recording = self._page(tabs, "Enregistrement")
        self._check(recording, "compact", "Réduire StepWatch en barre flottante pendant l’enregistrement")
        position = ttk.Frame(recording, style="Card.TFrame")
        position.grid(sticky="w", pady=(2, 10), padx=(24, 0))
        ttk.Label(position, text="Position de la barre :", style="Card.TLabel").pack(side="left", padx=(0, 8))
        ttk.Combobox(position, textvariable=self.vars["bar_position"], values=tuple(BAR_POSITIONS.values()),
                     state="readonly", width=20).pack(side="left")
        hotkeys = self._check(recording, "global_hotkeys",
                              "Raccourcis Ctrl+Maj+F9 / F10 / F11 actifs dans toutes les applications")
        if sys.platform != "win32":
            hotkeys.configure(state="disabled")
            self._hint(recording, "Sous Linux, ces raccourcis fonctionnent dans les fenêtres de StepWatch.")
        self._check(recording, "sound", "Signal sonore à chaque étape enregistrée")

        guide = self._page(tabs, "Guide et rapports")
        self._check(guide, "open_guide_after_stop", "Ouvrir l’onglet Guide à l’arrêt de l’enregistrement")
        self._check(guide, "auto_standalone", "Créer le rapport autonome automatiquement à l’arrêt")
        self._check(guide, "confirm_delete", "Demander confirmation avant de supprimer une étape")
        self._check(guide, "keep_retired", "Conserver la capture d’une étape supprimée (captures/retirees/)")
        self._hint(guide, "Décoché : la capture est effacée définitivement et la suppression ne peut pas être "
                          "annulée. StepWatch demande alors toujours confirmation.")

        interface = self._page(tabs, "Interface")
        size = ttk.Frame(interface, style="Card.TFrame")
        size.grid(sticky="w")
        ttk.Label(size, text="Taille du texte :", style="Card.TLabel").pack(side="left", padx=(0, 8))
        ttk.Combobox(size, textvariable=self.vars["font_scale"], state="readonly", width=8,
                     values=tuple(f"{value} %" for value in CHOICES["font_scale"])).pack(side="left")
        self._hint(interface, "Appliquée au prochain lancement de StepWatch.")
        self._check(interface, "advanced_open", "Afficher les réglages avancés au démarrage")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Réinitialiser", command=self._reset).pack(side="left")
        ttk.Button(buttons, text="Enregistrer", style="Primary.TButton", command=self._apply).pack(side="right")
        ttk.Button(buttons, text="Annuler", command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._apply())
        name.focus_set()

    # ----- Construction ------------------------------------------------------

    def _page(self, tabs: ttk.Notebook, title: str) -> ttk.Frame:
        page = ttk.Frame(tabs, style="Card.TFrame", padding=16)
        page.columnconfigure(0, weight=1)
        tabs.add(page, text=title)
        return page

    def _check(self, parent: ttk.Frame, key: str, text: str) -> ttk.Checkbutton:
        button = ttk.Checkbutton(parent, text=text, variable=self.vars[key])
        button.grid(sticky="w", pady=(0, 6))
        return button

    def _hint(self, parent: ttk.Frame, text: str) -> None:
        ttk.Label(parent, text=text, style="CardMuted.TLabel", wraplength=420).grid(sticky="w", pady=(0, 10))

    def _update_preview(self) -> None:
        self.preview.configure(text=f"{{date}} et {{heure}} sont remplacés. Exemple : « "
                                    f"{expand_name(self.vars['name_template'].get())} »")

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Dossier des guides")
        if selected:
            self.vars["output"].set(selected)

    # ----- Résultat ------------------------------------------------------------

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, variable in self.vars.items():
            result[key] = variable.get()
        positions = {label: key for key, label in BAR_POSITIONS.items()}
        result["bar_position"] = positions.get(result["bar_position"], "bas")
        try:
            result["font_scale"] = int(str(result["font_scale"]).rstrip(" %"))
        except ValueError:
            result["font_scale"] = self._initial_scale
        return result

    def _apply(self) -> None:
        self._on_apply(self.values())
        self.destroy()

    def _reset(self) -> None:
        self._on_reset()
        self.destroy()
