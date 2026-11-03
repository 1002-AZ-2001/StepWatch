"""Interface française. Tous les appels Tk restent sur le thread principal."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

from PIL import Image, ImageTk

from .engine import Recorder
from .models import CaptureConfig, CaptureTarget, Rect
from .platform import WindowsBackend
from .storage import SessionStore, import_sharex


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

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("StepWatch — Documentation d’intervention")
        self.root.geometry("1190x820")
        self.root.minsize(1020, 760)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._backend = WindowsBackend()
        self._recorder: Recorder | None = None
        self._targets: list[CaptureTarget] = []
        self._last_image: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._session_dir: Path | None = None
        self._import_thread: threading.Thread | None = None
        self._background_events: queue.Queue = queue.Queue()
        self._closing = False
        self._poll_id: str | None = None
        self._settings_widgets: list[tuple[tk.Widget, str]] = []

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
        self._refresh_targets()
        self._poll()

    def _style(self) -> None:
        families = {family.casefold() for family in tkfont.families(self.root)}
        self._ui_font = "Segoe UI" if "segoe ui" in families else "Helvetica"
        self._mono_font = "Consolas" if "consolas" in families else "Courier"
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(bg="#eef2f7")
        style.configure("TFrame", background="#eef2f7")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#eef2f7", foreground="#17263c", font=(self._ui_font, 10))
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("Muted.TLabel", foreground="#53657b")
        style.configure("Title.TLabel", font=(self._ui_font, 23, "bold"))
        style.configure("Section.TLabel", font=(self._ui_font, 11, "bold"))
        style.configure("Count.TLabel", background="#ffffff", font=(self._ui_font, 24, "bold"), foreground="#1459ac")
        style.configure("TButton", font=(self._ui_font, 10), padding=(12, 7))
        style.configure("Accent.TButton", background="#155eb9", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#0e4d9d"), ("disabled", "#a8bad0")])
        style.configure("TLabelframe", background="#eef2f7", padding=12)
        style.configure("TLabelframe.Label", background="#eef2f7", foreground="#17263c", font=(self._ui_font, 10, "bold"))
        style.configure("TCheckbutton", background="#eef2f7", font=(self._ui_font, 10))
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
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        heading = ttk.Frame(outer)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(heading, text="StepWatch", style="Title.TLabel").pack(side="left")
        ttk.Label(heading, text="Documentez vos interventions, étape par étape.", style="Muted.TLabel").pack(side="left", padx=18)
        self.import_button = ttk.Button(heading, text="Importer un dossier ShareX", command=self._import)
        self.import_button.pack(side="right")

        status = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10))
        status.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(status, textvariable=self.state_text, style="Card.TLabel", font=(self._ui_font, 11, "bold")).pack(side="left")
        ttk.Label(status, text="Local uniquement · aucune frappe clavier enregistrée", style="Card.TLabel").pack(side="right")

        body = ttk.Frame(outer)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=0, minsize=420)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        settings = ttk.Frame(body)
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        settings.rowconfigure(0, weight=1)
        settings.columnconfigure(0, weight=1)
        settings_canvas = tk.Canvas(settings, bg="#eef2f7", highlightthickness=0, width=420)
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
        ttk.Label(selection, text="Coordonnées en pixels physiques ; X et Y peuvent être négatifs.", style="Muted.TLabel", wraplength=380).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))

        capture = ttk.LabelFrame(left, text="Déclenchement")
        capture.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        capture.columnconfigure(0, weight=1)
        self._setting(ttk.Combobox(capture, textvariable=self.mode, values=tuple(self.MODES), state="readonly"), "readonly").grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        for col, (title, variable) in enumerate((("Intervalle (s)", self.interval), ("Délai clic (s)", self.delay), ("Seuil (%)", self.sensitivity))):
            ttk.Label(capture, text=title).grid(row=1, column=col, sticky="w", padx=(0, 8))
            self._setting(ttk.Entry(capture, textvariable=variable, width=9)).grid(row=2, column=col, sticky="ew", padx=(0, 8 if col < 2 else 0))
            capture.columnconfigure(col, weight=1)
        self._setting(ttk.Checkbutton(capture, text="Mode conservateur · détecter les petits changements", variable=self.conservative)).grid(row=3, column=0, columnspan=3, sticky="w", pady=(9, 0))
        ttk.Label(capture, text="Un seuil plus bas conserve davantage de changements visuels.", style="Muted.TLabel", wraplength=380).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))

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
        right.rowconfigure(2, weight=3)
        right.rowconfigure(4, weight=1)

        actions = ttk.Frame(right)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.start_button = ttk.Button(actions, text="Démarrer", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left", padx=(0, 6))
        self.pause_button = ttk.Button(actions, text="Pause", command=self._pause, state="disabled")
        self.pause_button.pack(side="left", padx=(0, 6))
        self.resume_button = ttk.Button(actions, text="Reprendre", command=self._resume, state="disabled")
        self.resume_button.pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(actions, text="Arrêter", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")

        counts = ttk.Frame(right, style="Card.TFrame", padding=(16, 8))
        counts.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for col, (label, variable) in enumerate((("Captures conservées", self.kept), ("Captures ignorées", self.ignored))):
            counts.columnconfigure(col, weight=1)
            ttk.Label(counts, text=label, style="Card.TLabel").grid(row=0, column=col, sticky="w")
            ttk.Label(counts, textvariable=variable, style="Count.TLabel").grid(row=1, column=col, sticky="w")

        preview = ttk.LabelFrame(right, text="Dernière capture conservée")
        preview.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.preview = tk.Label(preview, text="L’aperçu apparaîtra après la première capture.", bg="#142238", fg="#d7e2ef", font=(self._ui_font, 10), anchor="center")
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._draw_preview)

        session = ttk.Frame(right)
        session.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        session.columnconfigure(0, weight=1)
        ttk.Label(session, textvariable=self.session_text, style="Muted.TLabel", wraplength=430).grid(row=0, column=0, sticky="w")
        self.open_button = ttk.Button(session, text="Ouvrir le dossier", command=self._open_session, state="disabled")
        self.open_button.grid(row=0, column=1, padx=(6, 0))

        log_frame = ttk.LabelFrame(right, text="Journal")
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(log_frame, height=5, state="disabled", wrap="word", font=(self._mono_font, 9), relief="flat", bg="#ffffff", fg="#253b54")
        self.log.grid(row=0, column=0, sticky="nsew")
        footer = "Windows : les fenêtres doivent rester visibles et non recouvertes. Les masques sont appliqués avant l’enregistrement."
        if sys.platform != "win32":
            footer = "Capture native disponible sous Windows. L’import ShareX et les rapports restent utilisables ici."
        ttk.Label(outer, text=footer, style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 0))

    def _write_log(self, message: str) -> None:
        from datetime import datetime

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

    @staticmethod
    def _number(value: str) -> float:
        return float(value.strip().replace(",", "."))

    def _read_config(self) -> tuple[CaptureTarget, CaptureConfig]:
        if self.source.get() == "Zone personnalisée":
            rect = Rect(*(int(variable.get().strip()) for variable in self.region_values))
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
            mask = Rect(*(int(field.strip()) for field in fields))
            if mask.left < 0 or mask.top < 0:
                raise ValueError(f"Masque ligne {line_number} : X et Y doivent être positifs ou nuls.")
            masks.append(mask)
        config = CaptureConfig(
            mode=self.MODES[self.mode.get()],
            interval=self._number(self.interval.get()),
            click_delay=self._number(self.delay.get()),
            sensitivity=self._number(self.sensitivity.get()),
            conservative=self.conservative.get(),
            masks=tuple(masks),
        )
        return target, config

    def _active(self) -> bool:
        return self._recorder is not None and self._recorder.state in {"recording", "paused", "stopping"}

    def _start(self) -> None:
        if self._active() or self._closing:
            return
        store = None
        recorder = None
        try:
            if sys.platform != "win32":
                raise RuntimeError("L’enregistrement natif nécessite Windows. Vous pouvez importer un dossier ShareX sur ce système.")
            if not self.name.get().strip():
                raise ValueError("Indiquez un nom d’intervention.")
            if not self.output.get().strip():
                raise ValueError("Choisissez un dossier pour les rapports.")
            target, config = self._read_config()
            # Validation préalable : ne crée pas de dossier pour une cible déjà fermée.
            self._backend.resolve(target)
            store = SessionStore(Path(self.output.get()).expanduser(), self.name.get().strip())
            recorder = Recorder(target, config, store)
            self._recorder = recorder
            self._session_dir = store.directory
            self.session_text.set(str(store.directory))
            self.open_button.configure(state="normal")
            self.kept.set("0")
            self.ignored.set("0")
            self._last_image = None
            self._photo = None
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

    def _update_controls(self) -> None:
        state = self._recorder.state if self._recorder is not None else "idle"
        active = self._active()
        busy = self._closing or (self._import_thread is not None and self._import_thread.is_alive())
        self.start_button.configure(state="disabled" if active or busy else "normal")
        self.pause_button.configure(state="normal" if state == "recording" and not self._closing else "disabled")
        self.resume_button.configure(state="normal" if state == "paused" and not self._closing else "disabled")
        self.stop_button.configure(state="normal" if state in {"recording", "paused"} and not self._closing else "disabled")
        self.import_button.configure(state="disabled" if active or busy else "normal")
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
        else:
            self.state_text.set(self.STATE_NAMES.get(state, state))

    def _draw_preview(self, _event: tk.Event | None = None) -> None:
        if self._last_image is None:
            return
        width, height = max(self.preview.winfo_width() - 12, 1), max(self.preview.winfo_height() - 12, 1)
        preview = self._last_image.copy()
        preview.thumbnail((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(preview, master=self.root)
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
            self._write_log(str(message))

    def _poll(self) -> None:
        # Ne pas monopoliser Tk si beaucoup d’événements arrivent simultanément.
        if self._recorder is not None:
            for _ in range(100):
                try:
                    event = self._recorder.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(event)
        for _ in range(30):
            try:
                event = self._background_events.get_nowait()
            except queue.Empty:
                break
            if event["type"] == "import_done":
                self._session_dir = Path(event["directory"])
                self.session_text.set(str(self._session_dir))
                self.open_button.configure(state="normal")
                self._write_log(f"Import terminé. Sources préservées. Rapport : {self._session_dir}")
            elif event["type"] == "error":
                self._write_log(event["message"])
                if not self._closing:
                    messagebox.showerror("Import impossible", event["message"], parent=self.root)
        self._update_controls()
        self._poll_id = self.root.after(100, self._poll)

    def _import(self) -> None:
        if self._active() or self._closing or (self._import_thread and self._import_thread.is_alive()):
            return
        source = filedialog.askdirectory(parent=self.root, title="Dossier contenant les captures ShareX")
        if not source:
            return
        if not self.output.get().strip():
            messagebox.showerror("Dossier requis", "Choisissez un dossier pour les rapports.", parent=self.root)
            return
        output = Path(self.output.get()).expanduser()
        name = self.name.get().strip() or "Import ShareX"

        def work() -> None:
            try:
                directory = import_sharex(Path(source), output, name=name)
                self._background_events.put({"type": "import_done", "directory": str(directory)})
            except Exception as exc:
                self._background_events.put({"type": "error", "message": f"Import interrompu : {exc}"})

        self._import_thread = threading.Thread(target=work, name="stepwatch-import", daemon=False)
        self._import_thread.start()
        self._write_log("Import ShareX : copie et classement des images, sans modification des sources.")
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

    def _close(self) -> None:
        if self._closing:
            return
        if self._active() and not messagebox.askyesno("Fermer StepWatch", "Arrêter l’intervention et finaliser le rapport avant de fermer ?", parent=self.root):
            return
        self._closing = True
        self._stop()
        self._finish_close()

    def _finish_close(self) -> None:
        recorder_done = self._recorder is None or self._recorder.stop(timeout=0)
        importer_done = self._import_thread is None or not self._import_thread.is_alive()
        if not recorder_done or not importer_done:
            self.root.after(100, self._finish_close)
            return
        try:
            self._backend.close()
        except Exception as exc:
            self._write_log(f"Fermeture des ressources de sélection : {exc}")
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    StepWatchApp(root)
    root.mainloop()
