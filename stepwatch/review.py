"""Browse a local guide without loading its whole collection of images."""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .imaging import preview_image
from .storage import capture_path, read_session, step_trigger_label

PAGE_SIZE = 48
REVIEW_LIMIT = (1280, 800)


def _load_preview(directory, step):
    with Image.open(capture_path(directory, step), formats=("PNG",)) as original:
        original.load()
        pixels = original if original.mode == "RGB" else original.convert("RGB")
        return preview_image(pixels, REVIEW_LIMIT)


class SessionReview(ttk.Frame):
    """An indexed step list and one image. Workers never hold a Tk object."""

    def __init__(self, parent, *, choose_session, on_loaded, on_error, font):
        super().__init__(parent, padding=(0, 12, 0, 0))
        self._on_loaded, self._on_error = on_loaded, on_error
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="StepWatch-relecture")
        self._running = None
        self._pending = None
        self._generation = 0
        self.directory = None
        self.steps = []
        self._page = 0
        self._selected = None
        self._image = self._photo = None
        self._display_size = None

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, style="Card.TFrame", padding=(16, 9))
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        self.title = ttk.Label(header, text="Aucune session ouverte", style="CardTitle.TLabel", width=1)
        self.title.grid(row=0, column=0, sticky="ew")
        self.title.bind("<Configure>", lambda event: self.title.configure(wraplength=event.width))
        self.open_button = ttk.Button(header, text="Ouvrir une session", command=choose_session)
        self.open_button.grid(row=0, column=1, padx=(12, 0))

        listing = ttk.Frame(self, style="Card.TFrame", padding=12)
        listing.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.list = ttk.Treeview(listing, columns=("kind",), show="tree headings", selectmode="browse")
        self.list.heading("#0", text="Étape", anchor="w")
        self.list.column("#0", width=70, minwidth=54, stretch=False)
        self.list.heading("kind", text="Déclencheur", anchor="w")
        self.list.column("kind", width=215, minwidth=125)
        self.list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.list.configure(yscrollcommand=scroll.set)
        self.list.bind("<<TreeviewSelect>>", self._select_step)
        pager = ttk.Frame(listing, style="Card.TFrame")
        pager.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        self.previous = ttk.Button(pager, text="Précédent", command=lambda: self._show_page(self._page - 1))
        self.previous.pack(side="left")
        self.page_label = ttk.Label(pager, style="Card.TLabel", anchor="center")
        self.page_label.pack(side="left", fill="x", expand=True)
        self.next = ttk.Button(pager, text="Suivant", command=lambda: self._show_page(self._page + 1))
        self.next.pack(side="right")

        detail = ttk.Frame(self, style="Card.TFrame", padding=14)
        detail.grid(row=1, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(2, weight=1)
        self.step_title = ttk.Label(detail, text="Détail de l’étape", style="CardTitle.TLabel")
        self.step_title.grid(row=0, column=0, sticky="ew")
        self.step_time = ttk.Label(detail, style="CardMuted.TLabel")
        self.step_time.grid(row=1, column=0, sticky="w", pady=(4, 8))
        self.canvas = tk.Label(detail, text="Ouvrez une session pour revoir ses captures.",
                               bg="#132432", fg="#d9e9ee", width=1, height=1, font=(font, 10))
        self.canvas.grid(row=2, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self._render)
        self.annotation = tk.Text(detail, height=3, width=1, wrap="word", relief="flat",
                                  background="white", foreground="#213744", font=(font, 10), state="disabled")
        self.annotation.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._show_page(0)

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
        self._request("session", directory)

    def clear(self):
        self._generation += 1
        self._pending = None
        self.directory = None
        self.steps = []
        self._selected = None
        self.title.configure(text="Aucune session ouverte")
        self.step_title.configure(text="Détail de l’étape")
        self.step_time.configure(text="")
        self._set_annotation("")
        self._clear_image("Ouvrez une session pour revoir ses captures.")
        self._show_page(0)

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
                    name, _, self.steps = result
                    self.directory = directory
                    self._selected = None
                    self.title.configure(text=f"{name}  ·  {len(self.steps)} étape(s)")
                    self._clear_image("Cette session ne contient aucune étape.")
                    self.step_title.configure(text="Détail de l’étape")
                    self.step_time.configure(text="")
                    self._set_annotation("")
                    self._show_page(0)
                    self._on_loaded(directory)
                else:
                    self._image = result
                    self._render()
        self._dispatch()

    def _show_page(self, page):
        self._page = page
        children = self.list.get_children()
        if children:
            self.list.delete(*children)
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(self.steps))
        for position in range(start, end):
            step = self.steps[position]
            self.list.insert("", "end", iid=str(position), text=str(step["index"]),
                             values=(step_trigger_label(step),))
        self.page_label.configure(text=f"{start + 1}-{end} / {len(self.steps)}" if end else "0 étape")
        self.previous.configure(state="normal" if start else "disabled")
        self.next.configure(state="normal" if end < len(self.steps) else "disabled")
        if start < end:
            self.list.selection_set(str(start))
            self.list.focus(str(start))

    def _set_annotation(self, text):
        self.annotation.configure(state="normal")
        self.annotation.delete("1.0", "end")
        self.annotation.insert("1.0", text)
        self.annotation.configure(state="disabled")

    def _select_step(self, _event):
        if any(job is not None and job[1] == "session" for job in (self._running, self._pending)):
            return
        selected = self.list.selection()
        if not selected or selected[0] == self._selected or self.directory is None:
            return
        self._selected = selected[0]
        step = self.steps[int(selected[0])]
        self.step_title.configure(text=f"Étape {step['index']}  ·  {step_trigger_label(step)}")
        try:
            when = datetime.fromisoformat(step["timestamp"]).astimezone().strftime("%d/%m/%Y à %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            when = step["timestamp"]
        self.step_time.configure(text=when)
        self._set_annotation(step["annotation"] or "Aucune annotation")
        self._clear_image("Chargement de la capture…")
        self._request("preview", self.directory, step)

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
