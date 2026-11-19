"""Guide editor: review and edit a recorded session.

The step list is paged and a capture is decoded only when its step is
selected, so large sessions open quickly. Editing (titles, descriptions,
order, removal, redaction) goes through stepwatch.guide and is only allowed
when no recording is running. Workers never hold a Tk object.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from .guide import RETIRED_FOLDER, redact_step, remove_step, save_guide
from .imaging import preview_image
from .storage import StorageError, capture_path, read_session, step_heading, step_trigger_label
from .theme import PALETTE

PAGE_SIZE = 48
REVIEW_LIMIT = (1280, 800)
#: Largest capture shown in the redaction window.
REDACTION_LIMIT = (1100, 680)


def _load_preview(directory, step):
    with Image.open(capture_path(directory, step), formats=("PNG",)) as original:
        original.load()
        pixels = original if original.mode == "RGB" else original.convert("RGB")
        return preview_image(pixels, REVIEW_LIMIT)


class RedactionDialog(tk.Toplevel):
    """Draw black rectangles on a capture; returns them in image pixels."""

    def __init__(self, parent, image: Image.Image, title: str, font: str):
        super().__init__(parent)
        p = PALETTE
        self.title(title)
        self.configure(bg=p["bg"])
        self.transient(parent.winfo_toplevel())
        self.result: list[tuple[int, int, int, int]] | None = None
        self._factor = min(1.0, REDACTION_LIMIT[0] / image.width, REDACTION_LIMIT[1] / image.height)
        shown = image.resize((max(1, round(image.width * self._factor)), max(1, round(image.height * self._factor))),
                             Image.Resampling.LANCZOS) if self._factor < 1 else image
        self._photo = ImageTk.PhotoImage(shown, master=self)
        self._boxes: list[tuple[int, int, int, int]] = []
        self._items: list[int] = []
        self._start = None
        self._current = None

        frame = ttk.Frame(self, style="Card.TFrame", padding=16)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frame, text="Masquer une zone", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Faites glisser la souris sur chaque zone à noircir. Le masquage est définitif : "
                  "les pixels d’origine ne sont pas conservés.", style="CardMuted.TLabel",
                  wraplength=max(360, shown.width)).pack(anchor="w", pady=(4, 10))
        self.canvas = tk.Canvas(frame, width=shown.width, height=shown.height, highlightthickness=1,
                                highlightbackground=p["border"], cursor="crosshair", bg=p["preview"])
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.pack(fill="x", pady=(12, 0))
        self.undo_button = ttk.Button(buttons, text="Annuler la dernière zone", command=self._undo, state="disabled")
        self.undo_button.pack(side="left")
        self.apply_button = ttk.Button(buttons, text="Masquer définitivement", style="Primary.TButton",
                                       command=self._apply, state="disabled")
        self.apply_button.pack(side="right")
        ttk.Button(buttons, text="Annuler", command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.destroy())

    def _press(self, event):
        self._start = (event.x, event.y)
        self._current = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, fill="black",
                                                     outline=PALETTE["record"], stipple="gray50")

    def _drag(self, event):
        if self._current is not None:
            self.canvas.coords(self._current, *self._start, event.x, event.y)

    def _release(self, event):
        if self._current is None:
            return
        x0, y0 = self._start
        if abs(event.x - x0) < 3 or abs(event.y - y0) < 3:
            self.canvas.delete(self._current)
        else:
            self.canvas.itemconfigure(self._current, stipple="", outline="")
            box = tuple(round(value / self._factor) for value in (min(x0, event.x), min(y0, event.y),
                                                                  max(x0, event.x), max(y0, event.y)))
            self._boxes.append(box)
            self._items.append(self._current)
        self._current = None
        self._update_buttons()

    def _undo(self):
        if self._boxes:
            self._boxes.pop()
            self.canvas.delete(self._items.pop())
        self._update_buttons()

    def _update_buttons(self):
        state = "normal" if self._boxes else "disabled"
        self.undo_button.configure(state=state)
        self.apply_button.configure(state=state)

    def _apply(self):
        self.result = list(self._boxes)
        self.destroy()


class SessionReview(ttk.Frame):
    """Guide editor: indexed step list, one capture and editable texts."""

    def __init__(self, parent, *, choose_session, on_loaded, on_error, font,
                 on_changed=None, export=None):
        super().__init__(parent, padding=(0, 12, 0, 0))
        p = PALETTE
        self._font = font
        self._on_loaded, self._on_error = on_loaded, on_error
        self._on_changed = on_changed or (lambda _message: None)
        self._export = export
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="StepWatch-relecture")
        self._running = None
        self._pending = None
        self._generation = 0
        self.directory = None
        self.name = ""
        self.steps = []
        self._page = 0
        self._selected = None
        self._image = self._photo = None
        self._display_size = None
        self._editable = True
        self._filling = False
        self.guide_name = tk.StringVar()
        self.step_title_text = tk.StringVar()

        self.columnconfigure(0, weight=2, minsize=260)
        self.columnconfigure(1, weight=5)
        self.rowconfigure(1, weight=1)

        # En-tête du guide : titre modifiable et export, comme un document.
        header = ttk.Frame(self, style="Card.TFrame", padding=(16, 10))
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="TITRE DU GUIDE", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.name_entry = ttk.Entry(header, textvariable=self.guide_name, style="Title.TEntry",
                                    font=(font, 15, "bold"), state="disabled")
        self.name_entry.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.name_entry.bind("<Return>", lambda _event: self.focus_set())
        self.name_entry.bind("<FocusOut>", self._commit_name)
        self.title = ttk.Label(header, text="Aucun guide ouvert", style="CardMuted.TLabel")
        self.title.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.open_button = ttk.Button(header, text="Ouvrir un guide", command=choose_session)
        self.open_button.grid(row=0, column=1, rowspan=3, padx=(12, 0))
        self.export_button = ttk.Menubutton(header, text="Exporter le guide", state="disabled")
        menu = tk.Menu(self.export_button, tearoff=False, font=(font, 10))
        for label, kind in (("Rapport autonome (HTML, un seul fichier)", "standalone"),
                            ("Markdown (rapport.md)", "markdown"),
                            ("Ouvrir le dossier du guide", "folder")):
            menu.add_command(label=label, command=lambda kind=kind: self._export and self._export(kind))
        self.export_button.configure(menu=menu)
        self.export_button.grid(row=0, column=2, rowspan=3, padx=(8, 0))

        listing = ttk.Frame(self, style="Card.TFrame", padding=12)
        listing.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.list = ttk.Treeview(listing, columns=("title", "kind"), show="tree headings", selectmode="browse")
        self.list.heading("#0", text="N°", anchor="w")
        self.list.column("#0", width=52, minwidth=44, stretch=False)
        self.list.heading("title", text="Étape", anchor="w")
        self.list.column("title", width=190, minwidth=120)
        self.list.heading("kind", text="Type", anchor="w")
        self.list.column("kind", width=150, minwidth=90, stretch=False)
        self.list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.list.configure(yscrollcommand=scroll.set)
        self.list.bind("<<TreeviewSelect>>", self._select_step)
        pager = ttk.Frame(listing, style="Card.TFrame")
        pager.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        self.previous = ttk.Button(pager, text="‹ Précédent", command=lambda: self._show_page(self._page - 1))
        self.previous.pack(side="left")
        self.page_label = ttk.Label(pager, style="CardMuted.TLabel", anchor="center")
        self.page_label.pack(side="left", fill="x", expand=True)
        self.next = ttk.Button(pager, text="Suivant ›", command=lambda: self._show_page(self._page + 1))
        self.next.pack(side="right")

        detail = ttk.Frame(self, style="Card.TFrame", padding=16)
        detail.grid(row=1, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(3, weight=1)
        top = ttk.Frame(detail, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(2, weight=1)
        self.step_badge = tk.Label(top, text="", bg=p["primary"], fg="#ffffff", font=(font, 10, "bold"),
                                   padx=9, pady=2)
        self.step_badge.grid(row=0, column=0, sticky="w")
        self.step_kind = ttk.Label(top, text="", style="Chip.TLabel")
        self.step_kind.grid(row=0, column=1, sticky="w", padx=(8, 0))
        tools = ttk.Frame(top, style="Card.TFrame")
        tools.grid(row=0, column=3, sticky="e")
        self.up_button = ttk.Button(tools, text="↑", width=3, style="Icon.TButton", command=lambda: self._move(-1))
        self.down_button = ttk.Button(tools, text="↓", width=3, style="Icon.TButton", command=lambda: self._move(1))
        self.redact_button = ttk.Button(tools, text="Masquer une zone…", style="Icon.TButton", command=self._redact)
        self.delete_button = ttk.Button(tools, text="Supprimer", style="Icon.TButton", command=self._delete)
        for button in (self.up_button, self.down_button, self.redact_button, self.delete_button):
            button.pack(side="left", padx=(4, 0))
        self.step_title = ttk.Label(detail, text="Détail de l’étape", style="CardTitle.TLabel")  # Kept for compatibility.
        self.step_time = ttk.Label(detail, style="CardMuted.TLabel")
        self.step_time.grid(row=1, column=0, sticky="w", pady=(6, 8))
        self.title_entry = ttk.Entry(detail, textvariable=self.step_title_text, font=(font, 12, "bold"),
                                     state="disabled")
        self.title_entry.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.title_entry.bind("<Return>", lambda _event: self.focus_set())
        self.title_entry.bind("<FocusOut>", self._commit_step)
        self.title_hint = self.step_time  # The default title is shown on the time line.
        self.canvas = tk.Label(detail, text="Ouvrez un guide pour revoir et modifier ses étapes.",
                               bg=p["preview"], fg=p["preview_text"], width=1, height=1, font=(font, 10))
        self.canvas.grid(row=3, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self._render)
        ttk.Label(detail, text="DESCRIPTION", style="Section.TLabel").grid(row=4, column=0, sticky="w", pady=(12, 4))
        self.annotation = tk.Text(detail, height=3, width=1, wrap="word", relief="flat", bd=0,
                                  highlightthickness=1, highlightbackground=p["border_strong"],
                                  highlightcolor=p["accent"], background=p["surface"], foreground=p["text"],
                                  insertbackground=p["text"], font=(font, 10), padx=8, pady=6, state="disabled")
        self.annotation.grid(row=5, column=0, sticky="ew")
        self.annotation.bind("<FocusOut>", self._commit_step)
        self._show_page(0)
        self._update_tools()

    # ----- Chargement asynchrone -------------------------------------------------

    @property
    def busy(self):
        return self._running is not None or self._pending is not None

    def _request(self, kind, directory, step=None):
        self._generation += 1
        self._pending = self._generation, kind, directory, step
        self._dispatch()

    def _dispatch(self):
        if self._running is not None or self._pending is None:
            return
        request, self._pending = self._pending, None
        generation, kind, directory, step = request
        if kind == "session":
            future = self._executor.submit(read_session, directory)
        else:
            future = self._executor.submit(_load_preview, directory, step)
        self._running = generation, kind, directory, future

    def load(self, directory: Path):
        """Keep the current guide visible if the requested session is invalid."""
        self._commit_step()
        self._request("session", directory)

    def clear(self):
        self._generation += 1
        self._pending = None
        self.directory = None
        self.name = ""
        self.steps = []
        self._selected = None
        self.guide_name.set("")
        self.title.configure(text="Aucun guide ouvert")
        self._fill_step(None)
        self._clear_image("Ouvrez un guide pour revoir et modifier ses étapes.")
        self._show_page(0)
        self._update_tools()

    def poll(self):
        if self._running is None:
            self._dispatch()
            return
        generation, kind, directory, future = self._running
        if not future.done():
            return
        self._running = None
        if generation == self._generation:
            try:
                result = future.result()
            except Exception as exc:
                if kind == "preview":
                    self._clear_image("Capture indisponible")
                self._on_error(str(exc))
            else:
                if kind == "session":
                    self.name, _, self.steps = result
                    self.directory = directory
                    self._selected = None
                    self.guide_name.set(self.name)
                    self._update_count()
                    self._clear_image("Ce guide ne contient aucune étape." if not self.steps else "")
                    self._fill_step(None)
                    self._show_page(0)
                    self._on_loaded(directory)
                else:
                    self._image = result
                    self._display_size = None
                    self._render()
        self._update_tools()
        self._dispatch()

    # ----- Liste et sélection --------------------------------------------------

    def _update_count(self):
        count = len(self.steps)
        self.title.configure(text=f"{count} étape{'s' if count > 1 else ''} · modifications enregistrées automatiquement")

    def _row_values(self, step):
        kind = step_trigger_label(step)
        heading = step_heading(step)
        # The type column only adds information when a title was written.
        return heading, ("" if heading == kind else kind)

    def _show_page(self, page, select=None):
        self._page = page
        children = self.list.get_children()
        if children:
            self.list.delete(*children)
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(self.steps))
        for position in range(start, end):
            step = self.steps[position]
            self.list.insert("", "end", iid=str(position), text=str(step["index"]), values=self._row_values(step))
        self.page_label.configure(text=f"{start + 1}-{end} / {len(self.steps)}" if end else "0 étape")
        self.previous.configure(state="normal" if start else "disabled")
        self.next.configure(state="normal" if end < len(self.steps) else "disabled")
        target = select if select is not None and start <= select < end else (start if start < end else None)
        if target is not None:
            self._selected = None
            self.list.selection_set(str(target))
            self.list.focus(str(target))
            self.list.see(str(target))

    def _set_annotation(self, text):
        self.annotation.configure(state="normal")
        self.annotation.delete("1.0", "end")
        self.annotation.insert("1.0", text)
        self.annotation.edit_modified(False)
        self.annotation.configure(state="normal" if self._can_edit() else "disabled")

    def _fill_step(self, step):
        self._filling = True
        try:
            if step is None:
                self.step_badge.configure(text="")
                self.step_kind.configure(text="")
                self.step_time.configure(text="")
                self.step_title_text.set("")
                self._set_annotation("")
                return
            self.step_badge.configure(text=f"Étape {step['index']}")
            kind = step_trigger_label(step)
            self.step_kind.configure(text=kind)
            self.step_title.configure(text=f"Étape {step['index']}  ·  {step_heading(step)}")
            try:
                when = datetime.fromisoformat(step["timestamp"]).astimezone().strftime("%d/%m/%Y à %H:%M:%S")
            except (ValueError, OSError, OverflowError):
                when = step["timestamp"]
            self.step_time.configure(text=f"{when}  ·  titre de l’étape ci-dessous (vide : « {kind} »)")
            self.step_title_text.set(step.get("title") or "")
            self._set_annotation(step["annotation"] or "")
        finally:
            self._filling = False

    def _select_step(self, _event):
        if any(job is not None and job[1] == "session" for job in (self._running, self._pending)):
            return
        selected = self.list.selection()
        if not selected or selected[0] == self._selected or self.directory is None:
            return
        self._commit_step()
        self._selected = selected[0]
        step = self.steps[int(selected[0])]
        self._fill_step(step)
        self._clear_image("Chargement de la capture…")
        self._request("preview", self.directory, step)
        self._update_tools()

    def _clear_image(self, message):
        self.canvas.configure(image="", text=message)
        self._image = self._photo = None
        self._display_size = None

    def _render(self, _event=None):
        if self._image is None:
            return
        size = (self.canvas.winfo_width() - 12, self.canvas.winfo_height() - 12)
        if min(size) <= 1 or size == self._display_size:
            return
        self._photo = ImageTk.PhotoImage(preview_image(self._image, size), master=self)
        self.canvas.configure(image=self._photo, text="")
        self._display_size = size

    # ----- Édition ---------------------------------------------------------------

    def set_editable(self, editable: bool):
        """The application forbids edits while a recording or a background task runs."""
        if editable != self._editable:
            if not editable:
                self._commit_step()
            self._editable = editable
            self._update_tools()

    def _can_edit(self):
        return self._editable and self.directory is not None and not self.busy

    def _position(self):
        return int(self._selected) if self._selected is not None and self._selected.isdigit() else None

    def _update_tools(self):
        editable = self._can_edit()
        position = self._position()
        has_step = editable and position is not None and position < len(self.steps)
        self.name_entry.configure(state="normal" if editable else "disabled")
        self.export_button.configure(state="normal" if self.directory is not None and self._editable
                                     and not self.busy else "disabled")
        self.title_entry.configure(state="normal" if has_step else "disabled")
        self.annotation.configure(state="normal" if has_step else "disabled")
        self.up_button.configure(state="normal" if has_step and position > 0 else "disabled")
        self.down_button.configure(state="normal" if has_step and position < len(self.steps) - 1 else "disabled")
        for button in (self.redact_button, self.delete_button):
            button.configure(state="normal" if has_step else "disabled")

    def _save(self, steps, select=None, message=None):
        """Write the guide, reload the canonical steps and keep the selection."""
        try:
            deleted_standalone = save_guide(self.directory, self.guide_name.get(), steps)
            self.name, _, self.steps = read_session(self.directory)
        except (StorageError, OSError, ValueError) as exc:
            self._on_error(str(exc))
            return False
        self._after_change(deleted_standalone, select, message)
        return True

    def _after_change(self, deleted_standalone, select, message):
        self.guide_name.set(self.name)
        self._update_count()
        if select is not None:
            self._show_page(select // PAGE_SIZE, select=select)
        if message:
            self._on_changed(message)
        if deleted_standalone:
            self._on_changed("Le rapport autonome contenait l’ancienne version : il a été supprimé. "
                             "Exportez-le de nouveau pour le partager.")
        self._update_tools()

    def _commit_name(self, _event=None):
        if self._can_edit() and " ".join(self.guide_name.get().split()) != self.name:
            self._save(list(self.steps), message="Titre du guide enregistré.")

    def _commit_step(self, _event=None):
        """Save the selected step's title and description if they changed."""
        position = self._position()
        if self._filling or not self._can_edit() or position is None or position >= len(self.steps):
            return
        step = self.steps[position]
        title = " ".join(self.step_title_text.get().split())
        description = self.annotation.get("1.0", "end-1c").strip()
        if title == (step.get("title") or "") and description == (step["annotation"] or ""):
            return
        steps = [dict(item) for item in self.steps]
        steps[position].update(title=title, annotation=description)
        if self._save(steps):
            self.list.item(str(position), values=self._row_values(self.steps[position]))

    def _move(self, delta):
        self._commit_step()
        position = self._position()
        if not self._can_edit() or position is None:
            return
        target = position + delta
        if not 0 <= target < len(self.steps):
            return
        steps = list(self.steps)
        steps[position], steps[target] = steps[target], steps[position]
        self._save(steps, select=target, message=f"Étape déplacée en position {target + 1}.")

    def _delete(self):
        self._commit_step()
        position = self._position()
        if not self._can_edit() or position is None:
            return
        step = self.steps[position]
        if not messagebox.askyesno(
                "Supprimer l’étape",
                f"Retirer l’étape {step['index']} (« {step_heading(step)} ») du guide ?\n\n"
                f"Sa capture est déplacée dans captures/{RETIRED_FOLDER}/ et n’apparaît plus dans les rapports.",
                parent=self.winfo_toplevel()):
            return
        try:
            deleted_standalone = remove_step(self.directory, self.guide_name.get(), list(self.steps), position)
            self.name, _, self.steps = read_session(self.directory)
        except (StorageError, OSError, ValueError) as exc:
            self._on_error(str(exc))
            return
        if not self.steps:
            self._selected = None
            self._fill_step(None)
            self._clear_image("Ce guide ne contient plus aucune étape.")
            self._show_page(0)
        self._after_change(deleted_standalone, min(position, len(self.steps) - 1) if self.steps else None,
                           f"Étape {step['index']} retirée du guide (capture conservée dans captures/{RETIRED_FOLDER}/).")

    def _redact(self):
        self._commit_step()
        position = self._position()
        if not self._can_edit() or position is None:
            return
        step = self.steps[position]
        try:
            with Image.open(capture_path(self.directory, step), formats=("PNG",)) as original:
                image = original.convert("RGB")
        except (StorageError, OSError) as exc:
            self._on_error(str(exc))
            return
        dialog = RedactionDialog(self, image, f"Masquer une zone · étape {step['index']}", self._font)
        dialog.grab_set()
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            deleted_standalone = redact_step(self.directory, self.guide_name.get(), list(self.steps),
                                             position, dialog.result)
            self.name, _, self.steps = read_session(self.directory)
        except (StorageError, OSError, ValueError) as exc:
            self._on_error(str(exc))
            return
        self._after_change(deleted_standalone, position,
                           f"Étape {step['index']} : {len(dialog.result)} zone(s) masquée(s) définitivement.")
        self._selected = None
        self._select_step(None)

    def shutdown(self, timeout=0):
        self._pending = None
        if self._running is not None:
            # Wait for completion without consuming the result. An I/O error
            # raised by the worker does not mean it is still running.
            if wait((self._running[3],), timeout=max(0, timeout)).not_done:
                return False
            self._running = None
        self._executor.shutdown(wait=True)
        return True
