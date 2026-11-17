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
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, simpledialog, ttk

from PIL import Image, ImageTk

from .display import WINDOW_CLASS
from .engine import Recorder
from .imaging import preview_image
from .models import CaptureConfig, CaptureTarget, Rect
from .platform import WindowsBackend
from .review import SessionReview
from .storage import SessionStore, export_markdown, export_standalone, import_sharex

LOGGER = logging.getLogger(__name__)


class StepWatchApp:
    """Fenêtre principale ; les enregistrements ne démarrent que sur demande."""

    MODES = {
        "Hybride · clics et changements": "hybrid",
        "Clics locaux uniquement": "clicks",
        "Changements visuels uniquement": "visual",
    }
    SOURCES = ("Fenêtre", "Moniteur", "Zone personnalisée")
    STATE_NAMES = {
        "idle": "Prêt",
        "recording": "Enregistrement en cours",
        "paused": "En pause",
        "stopping": "Arrêt en cours…",
        "stopped": "Terminé",
        "error": "Erreur · consulter le journal",
    }

    def __init__(self, root: tk.Tk, log_path: Path | None = None) -> None:
        self.root = root
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
        self._backend = WindowsBackend()
        self._recorder: Recorder | None = None
        self._targets: list[CaptureTarget] = []
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

        self.name = tk.StringVar(value="Nouvelle intervention")
        self.output = tk.StringVar(value=str(Path.home() / "Documents" / "StepWatch"))
        self.source = tk.StringVar(value=self.SOURCES[0])
        self.target = tk.StringVar()
        self.mode = tk.StringVar(value=next(iter(self.MODES)))
        self.interval = tk.StringVar(value="1,0")
        self.delay = tk.StringVar(value="0,35")
        self.sensitivity = tk.StringVar(value="2,0")
        self.conservative = tk.BooleanVar(value=False)
        self.region_values = [tk.StringVar(value=value) for value in ("0", "0", "1280", "720")]
        self.state_text = tk.StringVar(value="Prêt · aucun enregistrement actif")
        self.kept = tk.StringVar(value="0")
        self.ignored = tk.StringVar(value="0")
        self.session_text = tk.StringVar(value="Le dossier et le rapport sont créés au démarrage.")

        self._style()
        self._build()
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
        families = {family.casefold(): family for family in tkfont.families(self.root)}
        default = tkfont.nametofont("TkDefaultFont", root=self.root).actual("family")
        fixed = tkfont.nametofont("TkFixedFont", root=self.root).actual("family")
        self._ui_font = next((families[name] for name in ("segoe ui", "dejavu sans", "liberation sans")
                              if name in families), default)
        self._mono_font = next((families[name] for name in ("consolas", "dejavu sans mono", "liberation mono")
                                if name in families), fixed)
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(bg="#eef4f5")
        style.configure("TFrame", background="#eef4f5")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Header.TFrame", background="#132c39")
        style.configure("TLabel", background="#eef4f5", foreground="#18333f", font=(self._ui_font, 10))
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("CardTitle.TLabel", background="#ffffff", font=(self._ui_font, 13, "bold"))
        style.configure("CardMuted.TLabel", background="#ffffff", foreground="#536874")
        style.configure("Muted.TLabel", foreground="#53657b")
        style.configure("Title.TLabel", background="#132c39", foreground="#ffffff", font=(self._ui_font, 19, "bold"))
        style.configure("Status.TLabel", background="#132c39", foreground="#c5ebdf", font=(self._ui_font, 10, "bold"))
        style.configure("Count.TLabel", background="#ffffff", font=(self._ui_font, 14, "bold"), foreground="#126c59")
        style.configure("TButton", font=(self._ui_font, 10), padding=(12, 7))
        style.configure("Accent.TButton", background="#147663", foreground="#ffffff")
        style.map("Accent.TButton", background=[("disabled", "#bdd0cf"), ("active", "#0b594a")],
                  foreground=[("disabled", "#3f5358")])
        style.configure("TNotebook", background="#eef4f5", borderwidth=0)
        style.configure("TNotebook.Tab", background="#e0ebed", foreground="#1d414d", padding=(18, 8),
                        font=(self._ui_font, 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#0b5e4d")])
        style.configure("Treeview", rowheight=self._px(31), font=(self._ui_font, 10), borderwidth=0)
        style.map("Treeview", background=[("selected", "#d5eee6")], foreground=[("selected", "#173b38")])
        style.configure("TLabelframe", background="#eef4f5", padding=12)
        style.configure("TLabelframe.Label", background="#eef4f5", foreground="#18333f", font=(self._ui_font, 10, "bold"))
        style.configure("TCheckbutton", background="#eef4f5", font=(self._ui_font, 10))
        style.configure("TEntry", font=(self._ui_font, 10))
        style.configure("TCombobox", font=(self._ui_font, 10))
        style.configure("TEntry", padding=5)
        style.configure("TCombobox", padding=5)

    def _setting(self, widget: tk.Widget, state: str = "normal") -> tk.Widget:
        if isinstance(widget, ttk.Entry):
            widget.configure(font=(self._ui_font, 10))
        self._settings_widgets.append((widget, state))
        return widget

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        heading = ttk.Frame(outer, style="Header.TFrame", padding=(16, 10))
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(heading, text="StepWatch", style="Title.TLabel").pack(side="left", padx=(0, 14))
        ttk.Label(heading, textvariable=self.state_text, style="Status.TLabel").pack(side="left")
        self.import_button = ttk.Button(heading, text="Importer ShareX", command=self._import)
        self.import_button.pack(side="right", padx=(8, 0))

        toolbar = ttk.Frame(outer, style="Card.TFrame", padding=(10, 6))
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        actions = ttk.Frame(toolbar, style="Card.TFrame")
        actions.pack(side="left")
        self.start_button = ttk.Button(actions, text="Démarrer", style="Accent.TButton", command=self._start)
        self.pause_button = ttk.Button(actions, text="Pause", command=self._pause, state="disabled")
        self.resume_button = ttk.Button(actions, text="Reprendre", command=self._resume, state="disabled")
        self.stop_button = ttk.Button(actions, text="Arrêter", command=self._stop, state="disabled")
        self.note_button = ttk.Button(actions, text="Ajouter une note", command=self._add_note, state="disabled")
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button, self.note_button):
            button.pack(side="left", padx=(0, 6))
        counts = ttk.Frame(toolbar, style="Card.TFrame")
        counts.pack(side="right", padx=(8, 0))
        for col, (label, variable) in enumerate((("Conservées", self.kept), ("Ignorées", self.ignored))):
            ttk.Label(counts, textvariable=variable, style="Count.TLabel").grid(row=0, column=2 * col, padx=(6, 4))
            ttk.Label(counts, text=label, style="CardMuted.TLabel").grid(row=0, column=2 * col + 1, padx=(0, 8))

        self._tabs = ttk.Notebook(outer)
        self._tabs.grid(row=2, column=0, sticky="nsew")
        body = ttk.Frame(self._tabs, padding=(0, 12, 0, 0))
        self._tabs.add(body, text="Capture")
        body.columnconfigure(0, weight=0, minsize=self._px(420))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        settings = ttk.Frame(body)
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        settings.rowconfigure(0, weight=1)
        settings.columnconfigure(0, weight=1)
        settings_canvas = tk.Canvas(settings, bg="#eef4f5", highlightthickness=0, width=self._px(420))
        settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scrollbar = ttk.Scrollbar(settings, orient="vertical", command=settings_canvas.yview)
        settings_scrollbar.grid(row=0, column=1, sticky="ns")
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        left = ttk.Frame(settings_canvas)
        settings_window = settings_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _event: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")))
        settings_canvas.bind("<Configure>", lambda event: settings_canvas.itemconfigure(settings_window, width=event.width))
        left.columnconfigure(0, weight=1)

        intervention = ttk.LabelFrame(left, text="Intervention")
        intervention.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        intervention.columnconfigure(0, weight=1)
        ttk.Label(intervention, text="Nom de l’intervention").grid(row=0, column=0, sticky="w")
        self._setting(ttk.Entry(intervention, textvariable=self.name)).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        ttk.Label(intervention, text="Dossier des rapports").grid(row=2, column=0, sticky="w")
        self._setting(ttk.Entry(intervention, textvariable=self.output)).grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self._setting(ttk.Button(intervention, text="...", width=3, command=self._choose_output)).grid(row=3, column=1, padx=(6, 0), pady=(4, 0))

        selection = ttk.LabelFrame(left, text="Zone de capture")
        selection.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        selection.columnconfigure(0, weight=1)
        choice = self._setting(ttk.Combobox(selection, textvariable=self.source, values=self.SOURCES, state="readonly"), "readonly")
        choice.grid(row=0, column=0, sticky="ew")
        choice.bind("<<ComboboxSelected>>", lambda _event: self._refresh_targets())
        self.refresh_button = self._setting(ttk.Button(selection, text="Actualiser", command=self._refresh_targets))
        self.refresh_button.grid(row=0, column=1, padx=(6, 0))
        self.target_combo = self._setting(ttk.Combobox(selection, textvariable=self.target, state="readonly"), "readonly")
        self.target_combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.region_frame = ttk.Frame(selection)
        self.region_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for index, label in enumerate(("X", "Y", "Largeur", "Hauteur")):
            self.region_frame.columnconfigure(index, weight=1)
            ttk.Label(self.region_frame, text=label).grid(row=0, column=index, sticky="w")
            self._setting(ttk.Entry(self.region_frame, textvariable=self.region_values[index], width=8)).grid(row=1, column=index, sticky="ew", padx=(0, 5 if index < 3 else 0))
        ttk.Label(selection, text="Coordonnées en pixels physiques ; X et Y peuvent être négatifs.", style="Muted.TLabel", wraplength=self._px(380)).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))

        capture = ttk.LabelFrame(left, text="Déclenchement")
        capture.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        capture.columnconfigure(0, weight=1)
        self._setting(ttk.Combobox(capture, textvariable=self.mode, values=tuple(self.MODES), state="readonly"), "readonly").grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        for col, (title, variable) in enumerate((("Intervalle (s)", self.interval), ("Délai clic (s)", self.delay), ("Seuil (%)", self.sensitivity))):
            ttk.Label(capture, text=title).grid(row=1, column=col, sticky="w", padx=(0, 8))
            self._setting(ttk.Entry(capture, textvariable=variable, width=9)).grid(row=2, column=col, sticky="ew", padx=(0, 8 if col < 2 else 0))
            capture.columnconfigure(col, weight=1)
        self._setting(ttk.Checkbutton(capture, text="Mode conservateur · détecter les petits changements", variable=self.conservative)).grid(row=3, column=0, columnspan=3, sticky="w", pady=(9, 0))
        ttk.Label(capture, text="Un seuil plus bas conserve davantage de changements visuels.", style="Muted.TLabel", wraplength=self._px(380)).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))

        masks = ttk.LabelFrame(left, text="Masquage des informations sensibles")
        masks.grid(row=3, column=0, sticky="ew")
        masks.columnconfigure(0, weight=1)
        ttk.Label(masks, text="Une zone par ligne : x, y, largeur, hauteur\nOrigine : coin supérieur gauche de la capture.", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.masks_text = tk.Text(masks, height=3, width=35, font=(self._mono_font, 10), relief="solid", bd=1, highlightthickness=0)
        self.masks_text.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self._setting(self.masks_text)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        preview = ttk.Frame(right, style="Card.TFrame", padding=14)
        preview.grid(row=0, column=0, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        ttk.Label(preview, text="Dernière capture", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        # The image must not determine the label's requested size.
        empty_preview = ("L’aperçu apparaîtra après la première capture." if sys.platform == "win32"
                         else "Importez un dossier ShareX pour le consulter dans Étapes.")
        self.preview = tk.Label(preview, text=empty_preview,
                                bg="#132432", fg="#d9e9ee", font=(self._ui_font, 10), width=1, height=1)
        self.preview.grid(row=1, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._draw_preview)

        self._review = SessionReview(self._tabs, choose_session=self._choose_session,
                                     on_loaded=self._use_session, on_error=self._review_error, font=self._ui_font)
        self._tabs.add(self._review, text="Étapes")
        self._tabs.bind("<<NotebookTabChanged>>", self._tab_changed)

        log_frame = ttk.Frame(self._tabs, padding=(0, 12, 0, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(log_frame, height=5, state="disabled", wrap="word",
                                             font=(self._mono_font, 9), relief="flat", bg="#ffffff", fg="#253b54")
        self.log.grid(row=0, column=0, sticky="nsew")
        self._tabs.add(log_frame, text="Journal")

        session = ttk.Frame(outer, style="Card.TFrame", padding=(10, 6))
        session.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        session.columnconfigure(0, weight=1)
        location = ttk.Label(session, textvariable=self.session_text, style="CardMuted.TLabel", width=1)
        location.grid(row=0, column=0, sticky="ew", padx=(4, 6))
        location.bind("<Configure>", lambda event: location.configure(wraplength=max(1, event.width - 6)))
        self.open_button = ttk.Button(session, text="Ouvrir le dossier", command=self._open_session, state="disabled")
        self.open_button.grid(row=0, column=1, padx=(6, 0))
        self.export_button = ttk.Button(session, text="Rapport autonome", command=self._export_standalone, state="disabled")
        self.export_button.grid(row=0, column=2, padx=(6, 0))

        footer = "Les fenêtres capturées doivent rester visibles. Les masques sont appliqués avant l’enregistrement."
        if sys.platform != "win32":
            footer = "Import ShareX, relecture et rapports disponibles. La capture nécessite Windows."
        ttk.Label(outer, text=footer, style="Muted.TLabel", wraplength=self._px(840)).grid(row=4, column=0, sticky="w", pady=(8, 0))

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
        if self._active():
            return
        custom = self.source.get() == "Zone personnalisée"
        self.region_frame.grid() if custom else self.region_frame.grid_remove()
        self.target_combo.configure(state="disabled" if custom else "readonly")
        self.refresh_button.configure(state="disabled" if custom else "normal")
        if custom:
            self._targets = []
            self.target.set("Coordonnées personnalisées")
            return
        previous = self.target.get()
        try:
            # L’énumération des fenêtres est indépendante de celle des moniteurs.
            self._targets = self._backend.list_windows() if self.source.get() == "Fenêtre" else self._backend.list_monitors()
        except Exception as exc:
            self._targets = []
            self._write_log(f"Énumération impossible : {exc}")
        labels = [f"{index + 1}. {target.label}" for index, target in enumerate(self._targets)]
        self.target_combo.configure(values=labels)
        self.target.set(previous if previous in labels else labels[0] if labels else "Aucune cible disponible")
        if not labels:
            self._write_log("Aucune cible. Actualisez la liste après avoir ouvert une fenêtre visible.")

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

    def _use_session(self, directory: Path) -> None:
        self._session_dir = directory
        self.session_text.set(str(directory))

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
        if self.source.get() == "Zone personnalisée":
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
            mode=self.MODES[self.mode.get()],
            interval=self._number(self.interval.get(), "Intervalle de comparaison"),
            click_delay=self._number(self.delay.get(), "Délai après clic"),
            sensitivity=self._number(self.sensitivity.get(), "Seuil de changement"),
            conservative=self.conservative.get(),
            masks=tuple(masks),
        )
        return target, config

    def _active(self) -> bool:
        return self._recorder is not None and self._recorder.state in {"recording", "paused", "stopping"}

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
            if sys.platform != "win32":
                raise RuntimeError("L’enregistrement natif nécessite Windows. Vous pouvez importer un dossier ShareX sur ce système.")
            if not self.name.get().strip():
                raise ValueError("Indiquez un nom d’intervention.")
            output = self._output_dir()
            target, config = self._read_config()
            # Validation préalable : ne crée pas de dossier pour une cible déjà fermée.
            self._backend.resolve(target)
            store = SessionStore(output, self.name.get().strip())
            recorder = Recorder(target, config, store)
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
            parent=self.root,
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
        active = state in {"recording", "paused", "stopping"}
        busy = self._closing or self._background_running()
        if (self._last_state in {"recording", "paused", "stopping"} and state == "stopped"
                and not self._closing):
            self._show_review()
            busy = self._background_running()
        self._last_state = state
        key = state, busy, self._closing, self._session_dir, self.source.get()
        if key == self._controls_key:
            return
        self.start_button.configure(state="disabled" if active or busy or sys.platform != "win32" else "normal")
        self.pause_button.configure(state="normal" if state == "recording" and not self._closing else "disabled")
        self.resume_button.configure(state="normal" if state == "paused" and not self._closing else "disabled")
        self.stop_button.configure(state="normal" if state in {"recording", "paused"} and not self._closing else "disabled")
        self.note_button.configure(state="normal" if state in {"recording", "paused"} and not self._closing else "disabled")
        self.import_button.configure(state="disabled" if active or busy else "normal")
        self._review.open_button.configure(state="disabled" if active or busy else "normal")
        self.open_button.configure(state="normal" if self._session_dir is not None else "disabled")
        exportable = self._session_dir is not None and not active and not busy
        self.export_button.configure(state="normal" if exportable else "disabled")
        for widget, normal_state in self._settings_widgets:
            widget.configure(state="disabled" if active or busy else normal_state)
        if not active and not busy:
            custom = self.source.get() == "Zone personnalisée"
            self.target_combo.configure(state="disabled" if custom else "readonly")
            self.refresh_button.configure(state="disabled" if custom else "normal")
        if self._closing:
            self.state_text.set("Fermeture en cours · finalisation des fichiers…")
        elif self._import_thread is not None and self._import_thread.is_alive():
            self.state_text.set("Import ShareX en cours…")
        elif self._export_thread is not None and self._export_thread.is_alive():
            self.state_text.set("Export du rapport autonome en cours…")
        else:
            self.state_text.set(self.STATE_NAMES.get(state, state))
        self._controls_key = key

    def _draw_preview(self, _event: tk.Event | None = None) -> None:
        if self._last_image is None:
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
        except Exception as exc:
            self._report_ui_error(exc)
        finally:
            # Toujours reprogrammer : sinon l’interface se fige et la file du
            # moteur grossit sans fin avec les aperçus des captures.
            if not self._disposed:
                delay = 100 if self._closing or self._active() or self._background_running() else 300
                self._poll_id = self.root.after(delay, self._poll)

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
        review = getattr(self, "_review", None)
        if review is not None and not review.shutdown():
            raise RuntimeError("La lecture de la capture est encore active.")
        callback = self.root.__dict__.get("report_callback_exception")
        if getattr(callback, "__self__", None) is self:
            del self.root.report_callback_exception
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
        app = StepWatchApp(root, log_path)
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
