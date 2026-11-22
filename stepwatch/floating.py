"""Barre flottante compacte affichée pendant l'enregistrement.

La fenêtre principale s'efface : le technicien garde l'application enregistrée
visible et pilote la session depuis une petite barre toujours au premier plan
(pause, note, arrêt). Les clics sur la barre ne deviennent jamais des étapes.
Sous Windows, la barre est exclue des captures ; ailleurs, elle est masquée
dans les captures quand elle chevauche la cible.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .theme import STATUS_COLOURS, set_role

STATE_TEXT = {
    "starting": "Démarrage…",
    "recording": "Enregistrement",
    "paused": "En pause",
    "stopping": "Arrêt…",
}


class StatusDot(QWidget):
    """Petit disque coloré de l'état d'enregistrement."""

    def __init__(self, size: int = 10, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._colour = QColor(STATUS_COLOURS["idle"][0])

    def set_colour(self, colour: str) -> None:
        self._colour = QColor(colour)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colour)
        painter.drawEllipse(1, 1, self.width() - 2, self.height() - 2)


class FloatingBar(QFrame):
    def __init__(self, *, icon, on_pause: Callable[[], None], on_resume: Callable[[], None],
                 on_note: Callable[[], None], on_stop: Callable[[], None], on_expand: Callable[[], None]):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("FloatingBar")
        self.setWindowTitle("StepWatch — enregistrement")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._on_pause, self._on_resume = on_pause, on_resume
        self._state = None
        self._drag_origin: QPoint | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(6)
        if icon is not None:
            logo = QLabel()
            logo.setPixmap(icon)
            layout.addWidget(logo)
        name = QLabel("StepWatch")
        name.setStyleSheet("font-weight: 700;")
        layout.addWidget(name)
        rule = QFrame()
        set_role(rule, "rule")
        rule.setFixedHeight(22)
        layout.addSpacing(4)
        layout.addWidget(rule)
        layout.addSpacing(4)
        self._dot = StatusDot(10)
        layout.addWidget(self._dot)
        self._status = QLabel("")
        self._status.setStyleSheet("font-weight: 700;")
        layout.addWidget(self._status)
        self._count = set_role(QLabel("0 étape"), "muted")
        layout.addWidget(self._count)
        layout.addSpacing(6)
        self.pause_button = set_role(QPushButton("Pause"), "compact")
        self.pause_button.clicked.connect(self._toggle_pause)
        self.note_button = set_role(QPushButton("+ Note"), "compact")
        self.note_button.clicked.connect(on_note)
        self.stop_button = QPushButton("■ Arrêter")
        set_role(self.stop_button, "record")
        self.stop_button.setStyleSheet("padding: 4px 10px;")
        self.stop_button.clicked.connect(on_stop)
        self.expand_button = set_role(QPushButton("⤢"), "icon")
        self.expand_button.setToolTip("Rouvrir la fenêtre de StepWatch")
        self.expand_button.clicked.connect(on_expand)
        for button in (self.pause_button, self.note_button, self.stop_button, self.expand_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(button)

    def show_at(self, position: str = "bas") -> None:
        """Centre de l'écran, en bas (au-dessus d'une barre des tâches) ou en haut."""
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        if area is not None:
            x = area.x() + max(0, (area.width() - self.width()) // 2)
            y = area.y() + 24 if position == "haut" else area.y() + max(0, area.height() - self.height() - 48)
            self.move(x, y)
        self.show()
        self.raise_()

    # Compatibilité avec l'ancienne barre Tk.
    show_bar = show_at

    def update_state(self, state: str, kept: str) -> None:
        if state != self._state:
            self._state = state
            self._dot.set_colour(STATUS_COLOURS.get(state, STATUS_COLOURS["idle"])[0])
            self._status.setText(STATE_TEXT.get(state, state))
            self.pause_button.setText("Reprendre" if state == "paused" else "Pause")
            self.pause_button.setEnabled(state in {"recording", "paused"})
            self.note_button.setEnabled(state in {"recording", "paused"})
            self.stop_button.setEnabled(state in {"starting", "recording", "paused"})
        count = int(kept) if str(kept).isdigit() else 0
        self._count.setText(f"{count} étape{'s' if count > 1 else ''}")

    def _toggle_pause(self) -> None:
        (self._on_resume if self._state == "paused" else self._on_pause)()

    # Déplacement à la souris (la barre n'a pas de barre de titre).
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                return  # Wayland : seul le compositeur peut déplacer la fenêtre.
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag_origin = None
