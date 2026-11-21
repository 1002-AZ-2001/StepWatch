"""Interface française. Tous les appels Tk restent sur le thread principal."""

from __future__ import annotations

import gc
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from PIL import Image, ImageTk

from .display import WINDOW_CLASS
from .engine import Recorder
from .floating import FloatingBar
from .hotkeys import LABELS as HOTKEY_LABELS, TK_SEQUENCES, GlobalHotkeys
from .imaging import preview_image
from .models import CaptureConfig, CaptureTarget, Rect
from .platform import capture_backend_factory, exclude_from_capture
from .review import SessionReview
from .storage import SessionStore, export_markdown, export_standalone, import_sharex
from .library import recent_guides
from .settings import Preferences
from .theme import PALETTE, STATUS_COLOURS, apply_theme, pick_fonts, render_icon

LOGGER = logging.getLogger(__name__)


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

    def __init__(self, root: tk.Tk, log_path: Path | None = None,
                 preferences: Preferences | None = None) -> None:
        self.root = root
        # Sans préférences (tests, auto-test), rien n'est lu ni écrit sur disque.
        self._prefs = preferences
        self._ui_thread = threading.get_ident()
        self._disposed = False
        # Sans console (exécutable --windowed), Tk écrirait ces erreurs sur
        # un sys.stderr absent : elles seraient perdues.
        self.root.report_callback_exception = self._report_callback_exception
        self.root.title("StepWatch — Documentation d’intervention")
        # Tk met les polices (en points) à l’échelle du DPI, pas les tailles en
        # pixels : sans ce facteur, à 150 % les réglages sont tronqués.
        self._scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self._fit_to_screen()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._backend_factory = capture_backend_factory()
        self._backend = self._backend_factory() if self._backend_factory is not None else None
        self._uses_portal = getattr(self._backend, "uses_portal", False) is True
        self._recorder: Recorder | None = None
        self._targets: list[CaptureTarget] = []
        self._listed_source: str | None = None
        self._needs_target_selection = False
        self._last_image: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._preview_key: tuple[Image.Image, int, int] | None = None
        self._session_dir: Path | None = None
        self._import_thread: threading.Thread | None = None
        self._export_thread: threading.Thread | None = None
        self._background_events: queue.Queue = queue.Queue()
        self._closing = False
        self._close_error: str | None = None
        self._poll_id: str | None = None
        self._settings_widgets: list[tuple[tk.Widget, str]] = []
        self._controls_key = None
        self._last_state = "idle"
        self._bar: FloatingBar | None = None
        self._bar_excluded = False
        self._compact_session = False
        self._withdrawn = False
        self._regions_key = None
        self._hotkeys = GlobalHotkeys() if GlobalHotkeys.supported() else None
        self._hotkeys_on = False

        self.name = tk.StringVar(value="Nouvelle intervention")
        self.output = tk.StringVar(value=str(Path.home() / "Documents" / "StepWatch"))
        self.source = tk.StringVar(value=self.SOURCES[0])
        self.target = tk.StringVar()
        self.mode = tk.StringVar(value=self.VISUAL_MODE if self._uses_portal else next(iter(self.MODES)))
        self.interval = tk.StringVar(value="1,0")
        self.delay = tk.StringVar(value="0,35")
        self.sensitivity = tk.StringVar(value="2,0")
        self.conservative = tk.BooleanVar(value=False)
        self.compact = tk.BooleanVar(value=True)
        self.region_values = [tk.StringVar(value=value) for value in ("0", "0", "1280", "720")]
        self.state_text = tk.StringVar(value="Prêt · aucun enregistrement actif")
        self.kept = tk.StringVar(value="0")
        self.ignored = tk.StringVar(value="0")
        self.session_text = tk.StringVar(value="Le dossier et le rapport sont créés au démarrage.")

        self._style()
        self._build()
        self._apply_preferences()
        # Mêmes combinaisons que les raccourcis globaux, dans les fenêtres de StepWatch.
        for action, sequence in TK_SEQUENCES.items():
            self.root.bind_all(sequence, lambda _event, action=action: self._hotkey(action), add="+")
        if log_path is not None:
            self._write_log(f"Journal de diagnostic : {log_path}", level=None)
        self._refresh_targets()
        self._poll()

    def _px(self, value: int) -> int:
        return int(round(value * self._scale))

    def _fit_to_screen(self) -> None:
        """Taille par défaut mise à l’échelle, bornée par l’écran principal.

        1190 × 820 dépassait un écran 1366 × 768, courant sur les portables
        d’intervention : le bas de la fenêtre (journal) était hors écran.
        """
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # Réserve la barre des tâches et la barre de titre.
        available_width = max(screen_width - self._px(40), 320)
        available_height = max(screen_height - self._px(110), 240)
        width = min(self._px(1190), available_width)
        height = min(self._px(820), available_height)
        self.root.minsize(min(self._px(1020), available_width), min(self._px(760), available_height))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (available_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _style(self) -> None:
        self._ui_font, self._mono_font = pick_fonts(self.root)
        apply_theme(self.root, self._ui_font, self._px(34))
        # Window/dock icon, drawn like packaging/linux/stepwatch.svg. An icon
        # must never prevent StepWatch from starting.
        try:
            self._icons = [ImageTk.PhotoImage(render_icon(self._px(size)), master=self.root) for size in (64, 32, 16)]
            self.root.iconphoto(True, *self._icons)
            self._header_icon = ImageTk.PhotoImage(render_icon(self._px(34)), master=self.root)
            self._bar_icon = ImageTk.PhotoImage(render_icon(self._px(20)), master=self.root)
        except (tk.TclError, ImportError) as exc:
            LOGGER.warning("Icône indisponible : %s", exc)
            self._icons, self._header_icon, self._bar_icon = [], "", None

    def _setting(self, widget: tk.Widget, state: str = "normal") -> tk.Widget:
        if isinstance(widget, ttk.Entry):
            widget.configure(font=(self._ui_font, 10))
        self._settings_widgets.append((widget, state))
        return widget

    def _section(self, parent: tk.Widget, row: int, title: str, *, pady=(0, 10)) -> ttk.Frame:
        """Carte blanche avec un titre de section discret, comme un groupe de réglages."""
        card = ttk.Frame(parent, style="Card.TFrame", padding=(16, 12, 16, 14))
        card.grid(row=row, column=0, sticky="ew", pady=pady)
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=title.upper(), style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        body = ttk.Frame(card, style="Card.TFrame")
        body.grid(row=1, column=0, columnspan=4, sticky="ew")
        body.columnconfigure(0, weight=1)
        return body

    def _build(self) -> None:
        p = PALETTE
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        # En-tête : identité, état de l’enregistrement, actions de session.
        heading = ttk.Frame(outer, style="Header.TFrame", padding=(20, 12))
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, image=self._header_icon, style="Card.TLabel").pack(side="left", padx=(0, 10))
        ttk.Label(heading, text="StepWatch", style="Title.TLabel").pack(side="left", padx=(0, 16))
        self._status = tk.Frame(heading, bg=p["bg"], padx=10, pady=3)
        self._status.pack(side="left")
        self._status_dot = tk.Canvas(self._status, width=self._px(10), height=self._px(10),
                                     highlightthickness=0, bg=p["bg"])
        self._status_dot.pack(side="left", padx=(0, 6))
        self._status_dot_id = self._status_dot.create_oval(1, 1, self._px(10) - 1, self._px(10) - 1, outline="")
        self._status_label = tk.Label(self._status, textvariable=self.state_text, bg=p["bg"],
                                      font=(self._ui_font, 9, "bold"))
        self._status_label.pack(side="left")
        self.import_button = ttk.Button(heading, text="Importer ShareX", command=self._import)
        self.import_button.pack(side="right", padx=(8, 0))
        self.open_session_button = ttk.Menubutton(heading, text="Ouvrir un guide", style="Secondary.TMenubutton")
        self._recent_menu = tk.Menu(self.open_session_button, tearoff=False, font=(self._ui_font, 10),
                                    postcommand=self._fill_recent_menu)
        self.open_session_button.configure(menu=self._recent_menu)
        self.open_session_button.pack(side="right")
        ttk.Frame(outer, style="Rule.TFrame", height=1).grid(row=1, column=0, sticky="new")

        content = ttk.Frame(outer, padding=(16, 14, 16, 10))
        content.grid(row=2, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        # Barre d’enregistrement : l’action principale reste toujours visible.
        toolbar = ttk.Frame(content, style="Card.TFrame", padding=(12, 10))
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        actions = ttk.Frame(toolbar, style="Card.TFrame")
        actions.pack(side="left")
        self.start_button = ttk.Button(actions, text="●  Enregistrer", style="Record.TButton", command=self._start)
        self.pause_button = ttk.Button(actions, text="Pause", command=self._pause, state="disabled")
        self.resume_button = ttk.Button(actions, text="Reprendre", command=self._resume, state="disabled")
        self.stop_button = ttk.Button(actions, text="■  Arrêter", command=self._stop, state="disabled")
        self.note_button = ttk.Button(actions, text="+  Note", command=self._add_note, state="disabled")
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button, self.note_button):
            button.pack(side="left", padx=(0, 8))
        counts = ttk.Frame(toolbar, style="Card.TFrame")
        counts.pack(side="right", padx=(8, 4))
        for col, (label, variable) in enumerate((("étapes", self.kept), ("ignorées", self.ignored))):
            ttk.Label(counts, textvariable=variable, style="Count.TLabel").grid(row=0, column=2 * col, padx=(14, 4))
            ttk.Label(counts, text=label, style="CardMuted.TLabel").grid(row=0, column=2 * col + 1)

        self._tabs = ttk.Notebook(content)
        self._tabs.grid(row=1, column=0, sticky="nsew")
        body = ttk.Frame(self._tabs, padding=(0, 12, 0, 0))
        self._tabs.add(body, text="Enregistrer")
        body.columnconfigure(0, weight=0, minsize=self._px(420))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        settings = ttk.Frame(body)
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        settings.rowconfigure(0, weight=1)
        settings.columnconfigure(0, weight=1)
        settings_canvas = tk.Canvas(settings, bg=p["bg"], highlightthickness=0, width=self._px(420))
        settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scrollbar = ttk.Scrollbar(settings, orient="vertical", command=settings_canvas.yview)
        settings_scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        left = ttk.Frame(settings_canvas)
        settings_window = settings_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _event: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")))
        settings_canvas.bind("<Configure>", lambda event: settings_canvas.itemconfigure(settings_window, width=event.width))
        left.columnconfigure(0, weight=1)

        intervention = self._section(left, 0, "Intervention")
        ttk.Label(intervention, text="Nom du guide", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self._setting(ttk.Entry(intervention, textvariable=self.name)).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        ttk.Label(intervention, text="Dossier des guides", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self._setting(ttk.Entry(intervention, textvariable=self.output)).grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self._setting(ttk.Button(intervention, text="Parcourir…", command=self._choose_output)).grid(row=3, column=1, padx=(8, 0), pady=(4, 0))

        selection = self._section(left, 1, "Que capturer ?")
        if self._uses_portal:
            ttk.Label(selection, text="Le portail Wayland vous propose de choisir un écran ou une fenêtre au démarrage.",
                      style="CardMuted.TLabel", wraplength=self._px(370)).grid(sticky="w")
        else:
            self._build_target_choices(selection)

        capture = self._section(left, 2, "Déclenchement")
        if self._uses_portal:
            ttk.Label(capture, text=self.VISUAL_MODE, style="Card.TLabel").grid(row=0, column=0, sticky="w")
        else:
            self._setting(ttk.Combobox(capture, textvariable=self.mode, values=tuple(self.MODES), state="readonly"), "readonly").grid(row=0, column=0, sticky="ew")
        ttk.Label(capture, text="Les clics et les changements visibles deviennent des étapes. Aucune frappe clavier n’est enregistrée.",
                  style="CardMuted.TLabel", wraplength=self._px(370)).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._setting(ttk.Checkbutton(capture, text="Barre flottante pendant l’enregistrement",
                                      variable=self.compact)).grid(row=2, column=0, sticky="w", pady=(10, 0))

        # Réglages avancés repliés par défaut : la plupart des interventions s’en passent.
        self._advanced_open = tk.BooleanVar(value=False)
        self.advanced_button = ttk.Button(left, text="▸  Réglages avancés", style="Link.TButton", command=self._toggle_advanced)
        self.advanced_button.grid(row=3, column=0, sticky="w", padx=(4, 0), pady=(0, 6))
        self._advanced = ttk.Frame(left)
        self._advanced.grid(row=4, column=0, sticky="ew")
        self._advanced.columnconfigure(0, weight=1)
        timing = self._section(self._advanced, 0, "Détection")
        fields = [("Intervalle (s)", self.interval)]
        if not self._uses_portal:
            fields.append(("Délai clic (s)", self.delay))
        fields.append(("Seuil (%)", self.sensitivity))
        for col, (title, variable) in enumerate(fields):
            ttk.Label(timing, text=title, style="Card.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 8))
            self._setting(ttk.Entry(timing, textvariable=variable, width=9)).grid(row=1, column=col, sticky="ew", padx=(0, 8 if col < len(fields) - 1 else 0), pady=(4, 0))
            timing.columnconfigure(col, weight=1)
        self._setting(ttk.Checkbutton(timing, text="Mode conservateur · détecter les petits changements", variable=self.conservative)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(timing, text="Un seuil plus bas conserve davantage de changements visuels.", style="CardMuted.TLabel", wraplength=self._px(370)).grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 0))
        masks = self._section(self._advanced, 1, "Masquage des informations sensibles")
        ttk.Label(masks, text="Une zone par ligne : x, y, largeur, hauteur, depuis le coin supérieur gauche de la capture. "
                  "Après l’enregistrement, l’éditeur du guide permet aussi de masquer une zone à la souris.",
                  style="CardMuted.TLabel", wraplength=self._px(370)).grid(row=0, column=0, sticky="w")
        self.masks_text = tk.Text(masks, height=3, width=35, font=(self._mono_font, 10), relief="flat", bd=0,
                                  highlightthickness=1, highlightbackground=p["border_strong"],
                                  highlightcolor=p["accent"], background=p["surface"], foreground=p["text"],
                                  padx=6, pady=4)
        self.masks_text.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._setting(self.masks_text)
        self._advanced.grid_remove()

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        preview = ttk.Frame(right, style="Card.TFrame", padding=16)
        preview.grid(row=0, column=0, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        ttk.Label(preview, text="Dernière capture", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        # The image must not determine the label's requested size.
        empty_preview = ("L’aperçu apparaîtra après la première étape.\nPendant l’enregistrement, StepWatch se réduit en une petite barre flottante.\n\n"
                         "Ctrl+Maj+F9 pause/reprise · Ctrl+Maj+F10 note · Ctrl+Maj+F11 arrêt"
                         + (" (dans toutes les applications)" if sys.platform == "win32" else "")
                         if self._backend is not None
                         else "Importez un dossier ShareX ou ouvrez un guide pour le modifier dans l’onglet Guide.")
        self.preview = tk.Label(preview, text=empty_preview, justify="center",
                                bg=p["preview"], fg=p["preview_text"], font=(self._ui_font, 10), width=1, height=1)
        self.preview.grid(row=1, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._draw_preview)
        self.preview.bind("<Map>", self._draw_preview)

        self._review = SessionReview(self._tabs, choose_session=self._choose_session,
                                     on_loaded=self._use_session, on_error=self._review_error, font=self._ui_font,
                                     on_changed=self._guide_changed, export=self._export_from_guide)
        self._tabs.add(self._review, text="Guide")
        self._tabs.bind("<<NotebookTabChanged>>", self._tab_changed)

        log_frame = ttk.Frame(self._tabs, padding=(0, 12, 0, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(log_frame, height=5, state="disabled", wrap="word",
                                             font=(self._mono_font, 9), relief="flat", bg=p["surface"], fg="#344054",
                                             padx=12, pady=10, highlightthickness=0)
        self.log.grid(row=0, column=0, sticky="nsew")
        self._tabs.add(log_frame, text="Journal")

        session = ttk.Frame(content, style="Card.TFrame", padding=(12, 8))
        session.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        session.columnconfigure(0, weight=1)
        location = ttk.Label(session, textvariable=self.session_text, style="CardMuted.TLabel", width=1)
        location.grid(row=0, column=0, sticky="ew", padx=(4, 6))
        location.bind("<Configure>", lambda event: location.configure(wraplength=max(1, event.width - 6)))
        self.open_button = ttk.Button(session, text="Ouvrir le dossier", command=self._open_session, state="disabled")
        self.open_button.grid(row=0, column=1, padx=(6, 0))
        self.export_button = ttk.Button(session, text="Rapport autonome", command=self._export_standalone, state="disabled")
        self.export_button.grid(row=0, column=2, padx=(6, 0))

        footer = "Les fenêtres capturées doivent rester visibles. Les masques sont appliqués avant l’enregistrement."
        if self._uses_portal:
            footer = "La source est partagée avec votre accord. Les masques sont appliqués avant l’enregistrement."
        elif self._backend is None:
            footer = "Import ShareX, guides et rapports disponibles. La capture nécessite Windows, X11 ou Wayland."
        ttk.Label(content, text=footer, style="Muted.TLabel", wraplength=self._px(840)).grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._set_status_colours("idle")

    def _toggle_advanced(self) -> None:
        opened = not self._advanced_open.get()
        self._advanced_open.set(opened)
        self._advanced.grid() if opened else self._advanced.grid_remove()
        self.advanced_button.configure(text=("▾" if opened else "▸") + "  Réglages avancés")

    def _set_status_colours(self, state: str) -> None:
        dot, background, text = STATUS_COLOURS.get(state, STATUS_COLOURS["idle"])
        for widget in (self._status, self._status_dot, self._status_label):
            widget.configure(bg=background)
        self._status_label.configure(fg=text)
        self._status_dot.itemconfigure(self._status_dot_id, fill=dot)

    def _build_target_choices(self, selection: ttk.LabelFrame) -> None:
        choice = self._setting(ttk.Combobox(selection, textvariable=self.source, values=self.SOURCES, state="readonly"), "readonly")
        choice.grid(row=0, column=0, sticky="ew")
        choice.bind("<<ComboboxSelected>>", lambda _event: self._refresh_targets())
        self.refresh_button = self._setting(ttk.Button(selection, text="Actualiser", command=self._refresh_targets))
        self.refresh_button.grid(row=0, column=1, padx=(6, 0))
        self.target_combo = self._setting(ttk.Combobox(selection, textvariable=self.target, state="readonly"), "readonly")
        self.target_combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.region_frame = ttk.Frame(selection, style="Card.TFrame")
        self.region_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for index, label in enumerate(("X", "Y", "Largeur", "Hauteur")):
            self.region_frame.columnconfigure(index, weight=1)
            ttk.Label(self.region_frame, text=label, style="Card.TLabel").grid(row=0, column=index, sticky="w")
            self._setting(ttk.Entry(self.region_frame, textvariable=self.region_values[index], width=8)).grid(row=1, column=index, sticky="ew", padx=(0, 5 if index < 3 else 0))
        ttk.Label(selection, text="Coordonnées en pixels physiques ; X et Y peuvent être négatifs.", style="CardMuted.TLabel", wraplength=self._px(370)).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))

    def _write_log(self, message: str, level: int | None = logging.INFO) -> None:
        """Ajoute une ligne au journal de l’interface et, si level, au journal de diagnostic."""
        from datetime import datetime

        if level is not None:
            LOGGER.log(level, message)

        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 1200:
            self.log.delete("1.0", "201.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_targets(self) -> None:
        if self._active() or self._backend is None or self._uses_portal:
            return
        source = self.source.get()
        custom = source == "Zone personnalisée"
        index = self.target_combo.current()
        previous = None
        if source != self._listed_source:
            self._listed_source = source
            self._needs_target_selection = False
        elif 0 <= index < len(self._targets):
            previous = self._targets[index]
        self.region_frame.grid() if custom else self.region_frame.grid_remove()
        self.target_combo.configure(state="disabled" if custom else "readonly")
        self.refresh_button.configure(state="disabled" if custom else "normal")
        if custom:
            self._targets = []
            self.target.set("Coordonnées personnalisées")
            self._update_controls()
            return
        try:
            # L’énumération des fenêtres est indépendante de celle des moniteurs.
            self._targets = self._backend.list_windows() if source == "Fenêtre" else self._backend.list_monitors()
        except Exception as exc:
            self._targets = []
            self._write_log(f"Énumération impossible : {exc}")
        labels = [f"{index + 1}. {target.label}" for index, target in enumerate(self._targets)]
        self.target_combo.configure(values=labels)
        if previous is not None:
            for index, target in enumerate(self._targets):
                if previous.kind == target.kind and (previous.hwnd == target.hwnd if target.kind == "window"
                                                     else previous.rect == target.rect):
                    self.target_combo.current(index)
                    self._needs_target_selection = False
                    break
            else:
                self._needs_target_selection = True
        elif labels and not self._needs_target_selection:
            self.target_combo.current(0)
        if not labels:
            self.target.set("Aucune cible disponible")
            self._write_log("Aucune cible. Actualisez la liste après avoir ouvert une fenêtre visible.")
        elif self._needs_target_selection:
            self.target.set("Sélectionnez une cible")
            if previous is not None:
                self._write_log("La cible sélectionnée a disparu. Choisissez une nouvelle cible.")
        self._update_controls()

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Dossier des interventions", initialdir=self.output.get() if Path(self.output.get()).is_dir() else None)
        if selected:
            self.output.set(selected)

    def _choose_session(self) -> None:
        if self._active() or self._background_running() or self._closing:
            return
        selected = filedialog.askdirectory(parent=self.root, title="Choisir un dossier contenant steps.json")
        if selected:
            self._review.load(Path(selected))
            self._update_controls()

    def _fill_recent_menu(self) -> None:
        """Guides récents : dossiers déjà ouverts et interventions du dossier des guides."""
        from datetime import datetime

        menu = self._recent_menu
        menu.delete(0, "end")
        remembered = self._prefs.values["recent"] if self._prefs is not None else ()
        entries = recent_guides(self.output.get().strip() or None, remembered)
        if entries:
            menu.add_command(label="Guides récents", state="disabled")
            for entry in entries:
                when = datetime.fromtimestamp(entry.modified).strftime("%d/%m/%Y %H:%M")
                menu.add_command(label=f"{entry.name}  ·  {when}",
                                 command=lambda directory=entry.directory: self._open_guide(directory))
            menu.add_separator()
        menu.add_command(label="Parcourir…", command=self._choose_session)

    def _open_guide(self, directory: Path) -> None:
        if self._active() or self._background_running() or self._closing:
            return
        self._review.load(Path(directory))
        self._tabs.select(self._review)
        self._update_controls()

    def _use_session(self, directory: Path) -> None:
        self._session_dir = directory
        self.session_text.set(str(directory))
        if self._prefs is not None:
            self._prefs.remember_guide(directory)
            self._prefs.save()

    # ----- Préférences ----------------------------------------------------------

    def _apply_preferences(self) -> None:
        if self._prefs is None:
            return
        values = self._prefs.values
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
            self.masks_text.delete("1.0", "end")
            self.masks_text.insert("1.0", values["masks"])
        if values["advanced_open"] != self._advanced_open.get():
            self._toggle_advanced()
        geometry = values["geometry"]
        if geometry:
            try:
                width, height = (int(part) for part in geometry.split("+")[0].split("-")[0].split("x"))
            except ValueError:
                width = height = 0
            # Ignore a size saved on a larger screen than the current one.
            if 0 < width <= self.root.winfo_screenwidth() and 0 < height <= self.root.winfo_screenheight():
                self.root.geometry(geometry)

    def _store_preferences(self) -> None:
        if self._prefs is None:
            return
        values = self._prefs.values
        values.update(output=self.output.get(), interval=self.interval.get(), delay=self.delay.get(),
                      sensitivity=self.sensitivity.get(), conservative=self.conservative.get(),
                      compact=self.compact.get(), source=self.source.get(), mode=self.mode.get(),
                      masks=self.masks_text.get("1.0", "end-1c"), advanced_open=self._advanced_open.get())
        try:
            if not self._withdrawn and self.root.state() == "normal":
                values["geometry"] = self.root.geometry()
        except tk.TclError:
            pass
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
        self._tabs.select(self._review)

    def _tab_changed(self, _event) -> None:
        if self._tabs.select() == str(self._review):
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
            index = self.target_combo.current()
            if index < 0 or index >= len(self._targets):
                raise ValueError("Sélectionnez une fenêtre ou un moniteur disponible.")
            target = self._targets[index]
        masks = []
        for line_number, line in enumerate(self.masks_text.get("1.0", "end").splitlines(), 1):
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

    def _active(self) -> bool:
        return self._recorder is not None and self._recorder.state in self.ACTIVE_STATES

    def _start(self) -> None:
        if self._active() or self._closing or self._background_running():
            return
        if self._recorder is not None:
            # Derniers événements de la session précédente (erreur de rapport
            # à l’arrêt, par exemple) : les afficher avant de la remplacer.
            self._drain(self._recorder.events, None, self._handle_event)
        store = None
        recorder = None
        try:
            if self._backend is None:
                raise RuntimeError("L’enregistrement est indisponible dans cette session. Vous pouvez importer un dossier ShareX.")
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
            self._tabs.select(0)
            self._use_session(store.directory)
            self.kept.set("0")
            self.ignored.set("0")
            self._last_image = None
            self._photo = None
            self._preview_key = None
            self.preview.configure(image="", text="En attente de la première capture…")
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

    def _background_running(self) -> bool:
        return self._review.busy or any(thread is not None and thread.is_alive()
                                        for thread in (self._import_thread, self._export_thread))

    def _update_controls(self) -> None:
        state = self._recorder.state if self._recorder is not None else "idle"
        active = state in self.ACTIVE_STATES
        busy = self._closing or self._background_running()
        if (self._last_state in self.ACTIVE_STATES and state == "stopped"
                and not self._closing):
            self._show_review()
            busy = self._background_running()
        self._last_state = state
        key = state, busy, self._closing, self._session_dir, self.source.get()
        if key == self._controls_key:
            return
        self.start_button.configure(state="disabled" if active or busy or self._backend is None else "normal")
        self.pause_button.configure(state="normal" if state == "recording" and not self._closing else "disabled")
        self.resume_button.configure(state="normal" if state == "paused" and not self._closing else "disabled")
        self.stop_button.configure(state="normal" if state in {"starting", "recording", "paused"} and not self._closing else "disabled")
        self.note_button.configure(state="normal" if state in {"recording", "paused"} and not self._closing else "disabled")
        self.import_button.configure(state="disabled" if active or busy else "normal")
        self._review.open_button.configure(state="disabled" if active or busy else "normal")
        self.open_session_button.configure(state="disabled" if active or busy else "normal")
        other_work = self._closing or any(thread is not None and thread.is_alive()
                                          for thread in (self._import_thread, self._export_thread))
        self._review.set_editable(not active and not other_work)
        self._set_status_colours("stopping" if self._closing else state)
        self.open_button.configure(state="normal" if self._session_dir is not None else "disabled")
        exportable = self._session_dir is not None and not active and not busy
        self.export_button.configure(state="normal" if exportable else "disabled")
        for widget, normal_state in self._settings_widgets:
            widget.configure(state="disabled" if active or busy else normal_state)
        if not active and not busy and not self._uses_portal:
            custom = self.source.get() == "Zone personnalisée"
            self.target_combo.configure(state="disabled" if custom or self._backend is None else "readonly")
            self.refresh_button.configure(state="disabled" if custom or self._backend is None else "normal")
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
        self._controls_key = key

    def _draw_preview(self, _event: tk.Event | None = None) -> None:
        if self._last_image is None or not self.preview.winfo_ismapped():
            return
        width, height = max(self.preview.winfo_width() - 12, 1), max(self.preview.winfo_height() - 12, 1)
        previous = self._preview_key
        if previous is not None and previous[0] is self._last_image and previous[1:] == (width, height):
            return  # Même image, même taille : rien à recalculer.
        preview = preview_image(self._last_image, (width, height))
        self._photo = ImageTk.PhotoImage(preview, master=self.root)
        self._preview_key = (self._last_image, width, height)
        self.preview.configure(image=self._photo, text="")

    def _handle_event(self, event: dict) -> None:
        if "kept" in event:
            self.kept.set(str(event["kept"]))
        if "ignored" in event:
            self.ignored.set(str(event["ignored"]))
        if event.get("type") == "saved" and event.get("image") is not None:
            self._last_image = event["image"]
            self._draw_preview()
        message = event.get("message", "")
        if message:
            if event.get("type") == "ignored" and message.startswith("Variation visuelle ignorée"):
                LOGGER.debug(message)  # The count already shows routine discarded samples.
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

    def _report_callback_exception(self, kind, value, traceback) -> None:
        LOGGER.error("Erreur dans un rappel de l’interface", exc_info=(kind, value, traceback))
        try:
            self._write_log(f"Erreur interne : {value}. Détails dans le journal de diagnostic.", level=None)
        except Exception:
            pass

    def _poll(self) -> None:
        if self._disposed:
            return
        try:
            # Une seule boucle de suivi, même si _poll est appelé directement.
            if self._poll_id is not None:
                self.root.after_cancel(self._poll_id)
            # Ne pas monopoliser Tk si beaucoup d’événements arrivent ensemble.
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
            # Toujours reprogrammer : sinon l’interface se fige et la file du
            # moteur grossit sans fin avec les aperçus des captures.
            if not self._disposed:
                delay = 100 if self._closing or self._active() or self._background_running() else 300
                self._poll_id = self.root.after(delay, self._poll)

    # ----- Barre flottante pendant l’enregistrement ------------------------------

    def _sync_floating_bar(self) -> None:
        recorder = self._recorder
        state = recorder.state if recorder is not None else "idle"
        if state in {"recording", "paused"} and self._compact_session and self._bar is None and not self._closing:
            self._bar = FloatingBar(self.root, font=self._ui_font, icon=self._bar_icon,
                                    on_pause=self._pause, on_resume=self._resume, on_note=self._add_note,
                                    on_stop=self._stop, on_expand=self._expand_from_bar, scale=self._scale)
            self._bar.show()
            self._bar.update_idletasks()
            self._bar_excluded = exclude_from_capture(self._window_handle(self._bar))
            self.root.withdraw()
            self._withdrawn = True
            self._regions_key = None
        if self._bar is not None:
            if state in self.ACTIVE_STATES and not self._closing:
                self._bar.update_state(state, self.kept.get())
            else:
                self._hide_floating_bar()
        if recorder is not None and state in self.ACTIVE_STATES:
            self._send_regions(recorder)

    # ----- Raccourcis pendant l’enregistrement --------------------------------

    def _sync_hotkeys(self) -> None:
        state = self._recorder.state if self._recorder is not None else "idle"
        hotkeys = self._hotkeys
        if hotkeys is None:
            return
        wanted = state in {"recording", "paused"} and not self._closing
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
            try:
                self._bar.destroy()
            except tk.TclError:
                pass
            self._bar = None
        self._compact_session = False
        if self._withdrawn:
            self._withdrawn = False
            self.root.deiconify()
            self.root.lift()

    def _expand_from_bar(self) -> None:
        """Rouvre la fenêtre principale ; la barre reste disponible."""
        if self._withdrawn:
            self._withdrawn = False
            self.root.deiconify()
            self.root.lift()

    @staticmethod
    def _window_handle(window: tk.Misc) -> int:
        if sys.platform != "win32":
            return 0
        try:
            return int(window.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return 0

    def _own_regions(self) -> tuple[list[Rect], list[Rect]]:
        """Rectangles écran de StepWatch : clics ignorés, et parties à masquer."""
        regions, masked = [], []
        windows = []
        if self._bar is not None and self._bar.winfo_ismapped():
            windows.append((self._bar, 0, 0, not self._bar_excluded))
        if not self._withdrawn and self.root.winfo_ismapped():
            # Marge pour la barre de titre et les bordures du gestionnaire de fenêtres.
            windows.append((self.root, self._px(8), self._px(40), True))
        for window, side, top, mask in windows:
            width, height = window.winfo_width(), window.winfo_height()
            if width <= 1 or height <= 1:
                continue
            rect = Rect(window.winfo_rootx() - side, window.winfo_rooty() - top, width + 2 * side, height + top + side)
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
        # A final report flush may have failed after PNGs were safely written.
        # Retry the matching session's in-memory steps before reading steps.json,
        # rather than exporting an apparently complete but stale report.
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
        if self._session_dir is None:
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(self._session_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self._session_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self._session_dir)])
        except OSError as exc:
            self._write_log(f"Ouverture du dossier impossible : {exc}")

    def shutdown(self, timeout: float = 5) -> bool:
        """Arrête les travailleurs avant de libérer Tk, sans les forcer."""
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
                    pass  # La fenêtre peut déjà avoir été détruite.
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
        for worker in (getattr(self, "_import_thread", None), getattr(self, "_export_thread", None)):
            if worker is not None and worker is not threading.current_thread() and worker.is_alive():
                worker.join(timeout=max(0, deadline - time.monotonic()))
                if worker.is_alive():
                    complete = False
                    LOGGER.warning("Le traitement %s est encore actif ; les ressources Tk sont conservées.", worker.name)
        review = getattr(self, "_review", None)
        if review is not None and not review.shutdown(max(0, deadline - time.monotonic())):
            complete = False
            LOGGER.warning("La lecture de la capture est encore active.")
        return complete

    def dispose(self) -> None:
        """Libère Tk sur son thread, après l’arrêt des travailleurs.

        Supprimer la méthode liée restaure celle de la classe Tk. Réaffecter
        une méthode liée à root créerait un autre cycle. Sans cette rupture,
        des StringVar/Tk peuvent être détruits plus tard par le GC d’un worker
        et provoquer Tcl_AsyncDelete sous Tk 8.6.
        """
        if getattr(self, "_disposed", False):
            return
        if threading.get_ident() != getattr(self, "_ui_thread", threading.get_ident()):
            raise RuntimeError("Les ressources Tk doivent être libérées sur le thread de l’interface.")
        hotkeys = getattr(self, "_hotkeys", None)
        if hotkeys is not None:
            hotkeys.stop()
        bar = getattr(self, "_bar", None)
        if bar is not None:
            try:
                bar.destroy()
            except tk.TclError:
                pass
            self._bar = None
        review = getattr(self, "_review", None)
        if review is not None and not review.shutdown():
            raise RuntimeError("La lecture de la capture est encore active.")
        callback = self.root.__dict__.get("report_callback_exception")
        if getattr(callback, "__self__", None) is self:
            del self.root.report_callback_exception
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:
                LOGGER.exception("Fermeture des ressources de sélection impossible")
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass  # Une fermeture externe peut déjà avoir détruit les widgets.
            self._poll_id = None
        try:
            exists = self.root.winfo_exists()
        except tk.TclError:
            exists = False
        if exists:
            self.root.destroy()
        self._photo = None
        self._preview_key = None
        self._last_image = None
        self._disposed = True

    def _close(self) -> None:
        if self._closing:
            return
        if self._active() and not messagebox.askyesno("Fermer StepWatch", "Arrêter l’intervention et finaliser le rapport avant de fermer ?", parent=self.root):
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
            # A transient stop/report/display error must not strand the closing
            # latch with every control disabled and no future close attempt.
            message = str(exc)
            if message != self._close_error:
                self._close_error = message
                LOGGER.exception("Fermeture incomplète ; nouvelle tentative")
                try:
                    self._write_log(f"Fermeture incomplète : {exc}. Nouvelle tentative…", level=None)
                except Exception:
                    pass
        finally:
            if retry:
                try:
                    self.root.after(100, self._finish_close)
                except tk.TclError:
                    # The main loop has gone away; run_app's finally still
                    # requests recorder shutdown and reports a timeout.
                    LOGGER.warning("La fenêtre a disparu avant la fin de la fermeture.")


def run_app(log_path: Path | None = None) -> None:
    root = tk.Tk(className=WINDOW_CLASS)
    app = None
    try:
        app = StepWatchApp(root, log_path, preferences=Preferences())
        root.mainloop()
    finally:
        # Ctrl+C dans la console ou exception sortie de la boucle Tk : sans
        # cet arrêt, le thread de capture (non démon) continuait d’enregistrer
        # sans interface et empêchait le processus de se terminer.
        try:
            if app is not None:
                if app.shutdown():
                    app.dispose()
            else:
                # Construction interrompue : le callback peut déjà référencer
                # l’instance partiellement construite.
                root.__dict__.pop("report_callback_exception", None)
                root.destroy()
        finally:
            # Les wrappers Tk restants sont collectés sur leur thread propriétaire,
            # après retrait des références locales, avant tout futur worker.
            app = None
            root = None
            gc.collect()
