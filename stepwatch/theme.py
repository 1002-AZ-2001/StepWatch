"""Apparence de StepWatch : palettes claire et sombre, feuille de style Qt et icône.

Surfaces sobres, une couleur d'accent, un bouton principal foncé et un bouton
d'enregistrement rouge. Le style Qt « Fusion » est utilisé partout, pour un
rendu identique sous Windows, X11 et Wayland. Les widgets choisissent leur
rôle par une propriété (``role``) lue par la feuille de style. L'icône
reproduit packaging/linux/stepwatch.svg.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

LIGHT = {
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
    "primary_text": "#ffffff",
    "record": "#dc2626",
    "record_hover": "#b91c1c",
    "danger_text": "#b42318",
    "hover": "#f2f4f7",
    "disabled_text": "#a0a8b8",
    "disabled_fill": "#d0d5dd",
    "preview": "#0f172a",
    "preview_text": "#cbd5e1",
    "log_text": "#344054",
}

DARK = {
    "bg": "#0f141c",
    "surface": "#171e29",
    "border": "#263042",
    "border_strong": "#34405a",
    "text": "#e6e9ef",
    "muted": "#94a0b4",
    "accent": "#5b8cff",
    "accent_soft": "#1f2c4d",
    "accent_text": "#c7d6ff",
    "primary": "#e6e9ef",
    "primary_hover": "#ffffff",
    "primary_text": "#0f141c",
    "record": "#e5484d",
    "record_hover": "#f2555a",
    "danger_text": "#ff8a8a",
    "hover": "#1f2836",
    "disabled_text": "#5b6678",
    "disabled_fill": "#2a3343",
    "preview": "#0a0e14",
    "preview_text": "#94a0b4",
    "log_text": "#c3cad6",
}

#: Palette en cours ; modifiée sur place par ``use_palette``.
PALETTE: dict[str, str] = dict(LIGHT)

#: Pastille d'état par état de l'enregistreur : (point, fond, texte), en clair puis en sombre.
_STATUS = {
    "recording": (("#dc2626", "#fdecec", "#8a1c1c"), ("#e5484d", "#3b1a1d", "#ffb3b5")),
    "starting": (("#d97706", "#fdf3e4", "#7a4706"), ("#f5a524", "#3a2a12", "#ffd89a")),
    "paused": (("#d97706", "#fdf3e4", "#7a4706"), ("#f5a524", "#3a2a12", "#ffd89a")),
    "stopping": (("#d97706", "#fdf3e4", "#7a4706"), ("#f5a524", "#3a2a12", "#ffd89a")),
    "error": (("#b42318", "#fdecec", "#8a1c1c"), ("#ff6b6b", "#3b1a1d", "#ffb3b5")),
    "stopped": (("#059669", "#e7f6f0", "#0b5e45"), ("#30c48d", "#123326", "#9ff0cf")),
    "idle": (("#98a2b3", "#eef1f5", "#344054"), ("#6b778c", "#1f2836", "#c3cad6")),
}
STATUS_COLOURS: dict[str, tuple[str, str, str]] = {state: pair[0] for state, pair in _STATUS.items()}

THEMES = ("clair", "sombre", "systeme")
#: Coche des cases (chemin à barres obliques, lu par la feuille de style Qt).
CHECK_ICON = (Path(__file__).resolve().parent / "assets" / "check.svg").as_posix()


def is_dark(choice: str, system_dark: bool) -> bool:
    return choice == "sombre" or (choice == "systeme" and system_dark)


def use_palette(dark: bool) -> None:
    """Choisit la palette claire ou sombre pour toute l'interface."""
    PALETTE.clear()
    PALETTE.update(DARK if dark else LIGHT)
    STATUS_COLOURS.clear()
    STATUS_COLOURS.update({state: pair[1 if dark else 0] for state, pair in _STATUS.items()})


def stylesheet(font: str, size: float = 10) -> str:
    """Feuille de style Qt de la palette en cours (taille de police en points)."""
    p = PALETTE
    pt = f"{size:g}pt"
    small = f"{max(6.5, size - 1.5):g}pt"
    big = f"{size + 3:g}pt"
    return f"""
* {{ font-family: "{font}"; font-size: {pt}; color: {p['text']}; }}
QMainWindow, QDialog, QWidget#Page {{ background: {p['bg']}; }}
QWidget[role="card"], QFrame[role="card"] {{ background: {p['surface']}; border: 1px solid {p['border']};
    border-radius: 8px; }}
QWidget[role="header"] {{ background: {p['surface']}; border-bottom: 1px solid {p['border']}; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {size + 6:g}pt; font-weight: 700; }}
QLabel[role="cardtitle"] {{ font-size: {big}; font-weight: 700; }}
QLabel[role="section"] {{ color: {p['muted']}; font-size: {small}; font-weight: 700; letter-spacing: 0.5px; }}
QLabel[role="muted"] {{ color: {p['muted']}; }}
QLabel[role="count"] {{ font-size: {big}; font-weight: 700; }}
QLabel[role="chip"] {{ background: {p['accent_soft']}; color: {p['accent_text']}; font-weight: 700;
    border-radius: 9px; padding: 2px 9px; font-size: {small}; }}
QLabel[role="badge"] {{ background: {p['primary']}; color: {p['primary_text']}; font-weight: 700;
    border-radius: 4px; padding: 2px 9px; }}
QLabel[role="preview"] {{ background: {p['preview']}; color: {p['preview_text']}; border-radius: 6px; }}
QLabel[role="status"] {{ font-weight: 700; border-radius: 11px; padding: 3px 10px; }}
QPushButton, QToolButton {{ background: {p['surface']}; border: 1px solid {p['border_strong']};
    border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover, QToolButton:hover {{ background: {p['hover']}; border-color: {p['muted']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {p['border']}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {p['accent']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {p['disabled_text']}; border-color: {p['border']};
    background: {p['surface']}; }}
QToolButton::menu-indicator {{ subcontrol-position: right center; right: 6px; }}
QToolButton[popupMode="2"] {{ padding-right: 24px; }}
QPushButton[role="primary"], QToolButton[role="primary"] {{ background: {p['primary']}; color: {p['primary_text']};
    border-color: {p['primary']}; font-weight: 700; }}
QPushButton[role="primary"]:hover {{ background: {p['primary_hover']}; }}
QPushButton[role="record"] {{ background: {p['record']}; color: #ffffff; border-color: {p['record']};
    font-weight: 700; }}
QPushButton[role="record"]:hover {{ background: {p['record_hover']}; }}
QPushButton[role="primary"]:disabled, QPushButton[role="record"]:disabled {{ background: {p['disabled_fill']};
    border-color: {p['disabled_fill']}; color: #ffffff; }}
QPushButton[role="icon"] {{ border-color: transparent; background: transparent; padding: 5px 8px; }}
QPushButton[role="icon"]:hover {{ background: {p['hover']}; border-color: {p['border']}; }}
QPushButton[role="link"] {{ border: none; background: transparent; color: {p['accent']}; font-weight: 700;
    padding: 4px 2px; text-align: left; }}
QPushButton[role="compact"] {{ padding: 4px 9px; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {{ background: {p['surface']}; border: 1px solid {p['border_strong']};
    border-radius: 6px; padding: 5px 7px; selection-background-color: {p['accent']}; selection-color: #ffffff; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {p['accent']}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{ background: {p['hover']}; color: {p['muted']}; }}
QLineEdit[role="title"] {{ font-size: {size + 5:g}pt; font-weight: 700; }}
QLineEdit[role="steptitle"] {{ font-size: {size + 2:g}pt; font-weight: 700; }}
QComboBox QAbstractItemView {{ background: {p['surface']}; border: 1px solid {p['border_strong']};
    selection-background-color: {p['accent_soft']}; selection-color: {p['accent_text']}; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {p['border_strong']}; border-radius: 4px;
    background: {p['surface']}; }}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; image: url("{CHECK_ICON}"); }}
QCheckBox::indicator:checked:disabled {{ background: {p['disabled_fill']}; border-color: {p['disabled_fill']}; }}
QCheckBox:disabled {{ color: {p['disabled_text']}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: 9px 18px; font-weight: 700;
    border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['accent']}; border-bottom-color: {p['accent']}; }}
QTabBar::tab:hover:!selected {{ color: {p['text']}; }}
QTreeWidget, QListWidget {{ background: {p['surface']}; border: none; alternate-background-color: {p['hover']}; }}
QTreeWidget::item {{ padding: 6px 2px; }}
QTreeWidget::item:selected {{ background: {p['accent_soft']}; color: {p['accent_text']}; }}
QHeaderView::section {{ background: {p['surface']}; color: {p['muted']}; border: none;
    border-bottom: 1px solid {p['border']}; padding: 6px; font-weight: 700; font-size: {small}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p['border_strong']}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {p['border_strong']}; border-radius: 5px; min-width: 24px; }}
QMenuBar {{ background: {p['surface']}; border-bottom: 1px solid {p['border']}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {p['accent_soft']}; color: {p['accent_text']}; border-radius: 4px; }}
QMenu {{ background: {p['surface']}; border: 1px solid {p['border_strong']}; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p['accent_soft']}; color: {p['accent_text']}; }}
QMenu::item:disabled {{ color: {p['disabled_text']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
QProgressBar {{ background: {p['hover']}; border: 1px solid {p['border']}; border-radius: 5px; height: 10px;
    text-align: center; }}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 4px; }}
QWidget#FloatingBar {{ background: {p['surface']}; border: 1px solid {p['border_strong']}; border-radius: 10px; }}
QFrame[role="rule"] {{ background: {p['border']}; max-width: 1px; min-width: 1px; border: none; }}
QToolTip {{ background: {p['primary']}; color: {p['primary_text']}; border: none; padding: 4px 6px; }}
"""


def pick_fonts(families: list[str]) -> tuple[str, str]:
    """Police d'interface et police à chasse fixe parmi celles installées."""
    known = {family.casefold(): family for family in families}
    ui = next((known[name] for name in ("segoe ui", "inter", "noto sans", "dejavu sans", "liberation sans",
                                        "cantarell", "ubuntu") if name in known), "Sans Serif")
    mono = next((known[name] for name in ("cascadia mono", "consolas", "dejavu sans mono", "liberation mono",
                                          "noto sans mono") if name in known), "Monospace")
    return ui, mono


def render_icon(size: int) -> Image.Image:
    """Icône StepWatch (tuile marine, fenêtre de rapport, montre rouge), dessinée à ``size``."""
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


def qicon():
    """Icône de fenêtre Qt (plusieurs tailles)."""
    from PySide6.QtGui import QIcon

    from .qtcompat import to_pixmap

    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(to_pixmap(render_icon(size)))
    return icon


def set_role(widget, role: str):
    """Rôle lu par la feuille de style ; renvoie le widget pour chaîner."""
    widget.setProperty("role", role)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    return widget


def qpalette():
    """Palette Qt assortie (traits et flèches dessinés par le style Fusion)."""
    from PySide6.QtGui import QColor, QPalette

    p = PALETTE
    palette = QPalette()
    roles = QPalette.ColorRole
    for role, key in ((roles.Window, "bg"), (roles.WindowText, "text"), (roles.Base, "surface"),
                      (roles.AlternateBase, "hover"), (roles.Text, "text"), (roles.Button, "surface"),
                      (roles.ButtonText, "text"), (roles.Highlight, "accent"), (roles.ToolTipBase, "primary"),
                      (roles.ToolTipText, "primary_text"), (roles.PlaceholderText, "muted"), (roles.Light, "border"),
                      (roles.Midlight, "border"), (roles.Mid, "border_strong"), (roles.Dark, "border_strong"),
                      (roles.Shadow, "border_strong"), (roles.Link, "accent")):
        palette.setColor(role, QColor(p[key]))
    palette.setColor(roles.HighlightedText, QColor("#ffffff"))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(p["disabled_text"]))
    return palette
