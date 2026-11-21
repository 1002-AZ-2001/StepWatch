"""Visual style of the interface: palette, ttk styles and the StepWatch icon.

Light surfaces, one accent colour, a dark primary button and a red record
button. Only ttk's portable "clam" theme is used, so the look is the same on
Windows, X11 and XWayland. The icon reproduces packaging/linux/stepwatch.svg.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk

from PIL import Image, ImageDraw

PALETTE = {
    "bg": "#f4f6fa",
    "surface": "#ffffff",
    "border": "#e2e6ee",
    "border_strong": "#cfd6e3",
    "text": "#111827",
    "muted": "#667085",
    "accent": "#2563eb",
    "accent_soft": "#eaf0ff",
    "accent_text": "#1d3a8f",
    "primary": "#111827",
    "primary_hover": "#273449",
    "record": "#dc2626",
    "record_hover": "#b91c1c",
    "danger_text": "#b42318",
    "hover": "#f2f4f7",
    "disabled_text": "#a0a8b8",
    "preview": "#0f172a",
    "preview_text": "#cbd5e1",
}

#: Status pill colours per recorder state: (dot, background, text).
STATUS_COLOURS = {
    "recording": ("#dc2626", "#fdecec", "#8a1c1c"),
    "starting": ("#d97706", "#fdf3e4", "#7a4706"),
    "paused": ("#d97706", "#fdf3e4", "#7a4706"),
    "stopping": ("#d97706", "#fdf3e4", "#7a4706"),
    "error": ("#b42318", "#fdecec", "#8a1c1c"),
    "stopped": ("#059669", "#e7f6f0", "#0b5e45"),
    "idle": ("#98a2b3", "#eef1f5", "#344054"),
}


def pick_fonts(root: tk.Misc) -> tuple[str, str]:
    families = {family.casefold(): family for family in tkfont.families(root)}
    default = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    fixed = tkfont.nametofont("TkFixedFont", root=root).actual("family")
    ui = next((families[name] for name in ("segoe ui", "inter", "noto sans", "dejavu sans", "liberation sans")
               if name in families), default)
    mono = next((families[name] for name in ("cascadia mono", "consolas", "dejavu sans mono", "liberation mono")
                 if name in families), fixed)
    return ui, mono


def apply_theme(root: tk.Misc, ui: str, row_height: int) -> ttk.Style:
    """Configure every ttk style used by StepWatch."""
    p = PALETTE
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.configure(bg=p["bg"])
    flat = {"bordercolor": p["border_strong"], "lightcolor": p["surface"], "darkcolor": p["surface"]}

    style.configure(".", font=(ui, 10), background=p["bg"], foreground=p["text"],
                    focuscolor=p["accent"])
    style.configure("TFrame", background=p["bg"])
    style.configure("Card.TFrame", background=p["surface"])
    style.configure("Header.TFrame", background=p["surface"])
    style.configure("Rule.TFrame", background=p["border"])
    style.configure("TLabel", background=p["bg"], foreground=p["text"], font=(ui, 10))
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Card.TLabel", background=p["surface"])
    style.configure("CardMuted.TLabel", background=p["surface"], foreground=p["muted"])
    style.configure("CardTitle.TLabel", background=p["surface"], font=(ui, 13, "bold"))
    style.configure("Section.TLabel", background=p["surface"], foreground=p["muted"], font=(ui, 8, "bold"))
    style.configure("Title.TLabel", background=p["surface"], foreground=p["text"], font=(ui, 16, "bold"))
    style.configure("Count.TLabel", background=p["surface"], foreground=p["text"], font=(ui, 13, "bold"))
    style.configure("Chip.TLabel", background=p["accent_soft"], foreground=p["accent_text"],
                    font=(ui, 9, "bold"), padding=(8, 2))

    style.configure("TButton", background=p["surface"], foreground=p["text"], padding=(14, 7),
                    relief="raised", borderwidth=1, focusthickness=0, font=(ui, 10), **flat)
    style.map("TButton", background=[("disabled", p["surface"]), ("pressed", p["border"]), ("active", p["hover"])],
              foreground=[("disabled", p["disabled_text"])],
              bordercolor=[("disabled", p["border"]), ("focus", p["accent"]), ("active", "#98a2b3")])
    for name, colour, hover in (("Primary", p["primary"], p["primary_hover"]),
                                ("Record", p["record"], p["record_hover"])):
        style.configure(f"{name}.TButton", background=colour, foreground="#ffffff", bordercolor=colour,
                        lightcolor=colour, darkcolor=colour, font=(ui, 10, "bold"))
        style.map(f"{name}.TButton", background=[("disabled", "#d0d5dd"), ("active", hover)],
                  foreground=[("disabled", "#ffffff")], bordercolor=[("disabled", "#d0d5dd"), ("active", hover)],
                  lightcolor=[("active", hover)], darkcolor=[("active", hover)])
    # Compact buttons of the floating recording bar.
    style.configure("Bar.TButton", padding=(8, 4))
    style.configure("BarRecord.TButton", padding=(8, 4))
    style.layout("BarRecord.TButton", style.layout("Record.TButton"))
    for option in ("background", "foreground", "bordercolor", "lightcolor", "darkcolor", "font"):
        style.configure("BarRecord.TButton", **{option: style.lookup("Record.TButton", option)})
    style.map("BarRecord.TButton", **{option: style.map("Record.TButton", option)
                                      for option in ("background", "foreground", "bordercolor",
                                                     "lightcolor", "darkcolor")})
    style.configure("Icon.TButton", padding=(8, 5), relief="flat", background=p["surface"], bordercolor=p["surface"],
                    lightcolor=p["surface"], darkcolor=p["surface"], font=(ui, 11))
    style.map("Icon.TButton", background=[("disabled", p["surface"]), ("active", p["hover"])],
              foreground=[("disabled", p["disabled_text"])], bordercolor=[("active", p["border"])])
    style.configure("Danger.TButton", foreground=p["danger_text"])
    style.configure("Link.TButton", relief="flat", background=p["surface"], foreground=p["accent"], padding=(0, 4),
                    bordercolor=p["surface"], lightcolor=p["surface"], darkcolor=p["surface"],
                    font=(ui, 10, "bold"))
    style.map("Link.TButton", background=[("active", p["surface"])], foreground=[("active", p["accent_text"])])
    style.configure("TMenubutton", background=p["primary"], foreground="#ffffff", padding=(14, 7),
                    relief="flat", arrowcolor="#ffffff", font=(ui, 10, "bold"),
                    bordercolor=p["primary"], lightcolor=p["primary"], darkcolor=p["primary"])
    style.map("TMenubutton", background=[("disabled", "#d0d5dd"), ("active", p["primary_hover"])])
    style.configure("Secondary.TMenubutton", background=p["surface"], foreground=p["text"], padding=(14, 7),
                    relief="raised", arrowcolor=p["muted"], font=(ui, 10),
                    bordercolor=p["border_strong"], lightcolor=p["surface"], darkcolor=p["surface"])
    style.map("Secondary.TMenubutton", background=[("disabled", p["surface"]), ("active", p["hover"])],
              foreground=[("disabled", p["disabled_text"])])

    field = {"fieldbackground": p["surface"], "bordercolor": p["border_strong"],
             "lightcolor": p["surface"], "darkcolor": p["surface"], "padding": 6}
    style.configure("TEntry", **field)
    style.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
              fieldbackground=[("disabled", p["hover"])], foreground=[("disabled", p["muted"])])
    style.configure("Title.TEntry", **dict(field, padding=(8, 4)), font=(ui, 15, "bold"))
    style.configure("TCombobox", **field, arrowcolor=p["muted"], background=p["surface"])
    style.map("TCombobox", fieldbackground=[("readonly", p["surface"]), ("disabled", p["hover"])],
              bordercolor=[("focus", p["accent"])], foreground=[("disabled", p["muted"])],
              selectbackground=[("readonly", p["surface"])], selectforeground=[("readonly", p["text"])])
    style.configure("TCheckbutton", background=p["surface"], font=(ui, 10), indicatorcolor=p["surface"])
    style.map("TCheckbutton", indicatorcolor=[("selected", p["accent"])], background=[("active", p["surface"])])

    style.configure("TNotebook", background=p["bg"], borderwidth=1, tabmargins=(0, 0, 0, 0),
                    bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"])
    style.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"], padding=(18, 9),
                    borderwidth=0, font=(ui, 10, "bold"), **{"lightcolor": p["bg"], "bordercolor": p["bg"]})
    style.map("TNotebook.Tab", background=[("selected", p["surface"]), ("active", p["hover"])],
              foreground=[("selected", p["accent"])], lightcolor=[("selected", p["surface"])],
              bordercolor=[("selected", p["border"])])

    style.configure("Treeview", background=p["surface"], fieldbackground=p["surface"], foreground=p["text"],
                    rowheight=row_height, font=(ui, 10), borderwidth=1, relief="flat",
                    bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"])
    style.map("Treeview", background=[("selected", p["accent_soft"])], foreground=[("selected", p["accent_text"])])
    style.configure("Treeview.Heading", background=p["surface"], foreground=p["muted"], relief="flat",
                    font=(ui, 9, "bold"), padding=(6, 6), bordercolor=p["border"],
                    lightcolor=p["surface"], darkcolor=p["surface"])
    style.map("Treeview.Heading", background=[("active", p["hover"])])
    style.configure("Vertical.TScrollbar", background=p["border"], troughcolor=p["surface"],
                    bordercolor=p["surface"], lightcolor=p["border"], darkcolor=p["border"],
                    arrowcolor=p["muted"], gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", p["border_strong"])])
    # Legacy LabelFrame styles stay readable if a section still uses them.
    style.configure("TLabelframe", background=p["surface"], padding=12, bordercolor=p["border"])
    style.configure("TLabelframe.Label", background=p["surface"], foreground=p["text"], font=(ui, 10, "bold"))
    return style


def render_icon(size: int) -> Image.Image:
    """StepWatch icon (navy tile, report window, red watch), drawn at ``size``."""
    scale = 4
    s = size * scale / 128
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(x0, y0, x1, y1):
        return tuple(round(value * s) for value in (x0, y0, x1, y1))

    draw.rounded_rectangle(box(4, 4, 124, 124), radius=round(26 * s), fill="#162a43")
    draw.rounded_rectangle(box(22, 26, 106, 102), radius=round(8 * s), fill="#f2f6fc")
    draw.line(box(22, 42, 106, 42), fill="#c6d5e8", width=max(1, round(4 * s)))
    for cx in (32, 42):
        draw.ellipse(box(cx - 3, 31, cx + 3, 37), fill="#3e86db")
    for y, x1 in ((58, 74), (71, 85), (84, 64)):
        draw.line(box(40, y, x1, y), fill="#3e86db", width=max(1, round(6 * s)))
    draw.ellipse(box(70, 67, 106, 103), fill="#e64d42")
    draw.line(box(88, 75, 88, 86), fill="white", width=max(1, round(4 * s)))
    draw.line(box(88, 86, 95, 90), fill="white", width=max(1, round(4 * s)))
    return image.resize((size, size), Image.Resampling.LANCZOS)
