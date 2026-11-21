"""Compact floating bar shown while recording.

The main window gets out of the way: the technician keeps the recorded
application visible and controls the session from a small always-on-top bar
(pause, note, stop). Clicks on the bar never become steps. On Windows the
bar is excluded from screen captures; elsewhere it is masked in captures
when it overlaps the target.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .theme import PALETTE, STATUS_COLOURS

STATE_TEXT = {
    "starting": "Démarrage…",
    "recording": "Enregistrement",
    "paused": "En pause",
    "stopping": "Arrêt…",
}


class FloatingBar(tk.Toplevel):
    def __init__(self, master: tk.Misc, *, font: str, icon: tk.PhotoImage | None,
                 on_pause: Callable[[], None], on_resume: Callable[[], None],
                 on_note: Callable[[], None], on_stop: Callable[[], None],
                 on_expand: Callable[[], None], scale: float = 1.0):
        super().__init__(master)
        p = PALETTE
        self._on_pause, self._on_resume = on_pause, on_resume
        self._state = None
        self.withdraw()
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        # 1 px border drawn by the outer frame; the window manager draws none.
        self.configure(bg=p["border_strong"])
        inner = tk.Frame(self, bg=p["surface"], padx=round(10 * scale), pady=round(7 * scale))
        inner.pack(padx=1, pady=1)
        self._drag_widgets = [inner]
        if icon is not None:
            logo = tk.Label(inner, image=icon, bg=p["surface"])
            logo.pack(side="left", padx=(0, 8))
            self._drag_widgets.append(logo)
        name = tk.Label(inner, text="StepWatch", bg=p["surface"], fg=p["text"], font=(font, 10, "bold"))
        name.pack(side="left")
        self._drag_widgets.append(name)
        tk.Frame(inner, bg=p["border"], width=1, height=round(22 * scale)).pack(side="left", padx=10)
        size = round(10 * scale)
        self._dot = tk.Canvas(inner, width=size, height=size, bg=p["surface"], highlightthickness=0)
        self._dot.pack(side="left", padx=(0, 6))
        self._dot_id = self._dot.create_oval(1, 1, size - 1, size - 1, outline="")
        self._status = tk.Label(inner, text="", bg=p["surface"], fg=p["text"], font=(font, 9, "bold"))
        self._status.pack(side="left")
        self._count = tk.Label(inner, text="0 étape", bg=p["surface"], fg=p["muted"], font=(font, 9))
        self._count.pack(side="left", padx=(10, 12))
        self._drag_widgets += [self._dot, self._status, self._count]
        self.pause_button = ttk.Button(inner, text="Pause", width=9, style="Bar.TButton", command=self._toggle_pause)
        self.pause_button.pack(side="left", padx=(0, 6))
        self.note_button = ttk.Button(inner, text="+ Note", width=7, style="Bar.TButton", command=on_note)
        self.note_button.pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(inner, text="■ Arrêter", width=9, style="BarRecord.TButton", command=on_stop)
        self.stop_button.pack(side="left", padx=(0, 6))
        self.expand_button = ttk.Button(inner, text="⤢", width=3, style="Icon.TButton", command=on_expand)
        self.expand_button.pack(side="left")
        for widget in self._drag_widgets:
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
        self._origin = (0, 0)

    def show(self, position: str = "bas") -> None:
        """Centre of the screen, at the bottom (above a typical task bar) or at the top."""
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = 24 if position == "haut" else max(0, self.winfo_screenheight() - height - 72)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.lift()

    def update_state(self, state: str, kept: str) -> None:
        if state != self._state:
            self._state = state
            dot, _background, _text = STATUS_COLOURS.get(state, STATUS_COLOURS["idle"])
            self._dot.itemconfigure(self._dot_id, fill=dot)
            self._status.configure(text=STATE_TEXT.get(state, state))
            self.pause_button.configure(text="Reprendre" if state == "paused" else "Pause",
                                        state="normal" if state in {"recording", "paused"} else "disabled")
            self.note_button.configure(state="normal" if state in {"recording", "paused"} else "disabled")
            self.stop_button.configure(state="normal" if state in {"starting", "recording", "paused"} else "disabled")
        count = int(kept) if str(kept).isdigit() else 0
        self._count.configure(text=f"{count} étape{'s' if count > 1 else ''}")

    def _toggle_pause(self) -> None:
        (self._on_resume if self._state == "paused" else self._on_pause)()

    def _start_drag(self, event) -> None:
        self._origin = (event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty())

    def _drag(self, event) -> None:
        self.geometry(f"+{event.x_root - self._origin[0]}+{event.y_root - self._origin[1]}")
