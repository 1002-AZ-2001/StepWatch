"""Éditeur de guide : relire et modifier une session enregistrée.

La liste des étapes est paginée et une capture n'est décodée que lorsque son
étape est sélectionnée : les longues sessions s'ouvrent vite. Les
modifications (titres, descriptions, ordre, suppression, masquage) passent par
stepwatch.guide et ne sont permises qu'en dehors d'un enregistrement. Les
traitements en arrière-plan ne manipulent jamais de widget Qt.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QSizePolicy,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .guide import RETIRED_FOLDER, redact_step, remove_step, restore_step, save_guide
from .imaging import preview_image
from .qtcompat import Value, messagebox, to_pixmap
from .storage import StorageError, capture_path, read_session, step_heading, step_trigger_label
from .theme import PALETTE, set_role

PAGE_SIZE = 48
#: Modifications que ``undo`` peut annuler (un masquage est définitif et n'y figure jamais).
UNDO_LIMIT = 50
_TEXT_WIDGETS = (QLineEdit, QPlainTextEdit, QComboBox)
REVIEW_LIMIT = (1280, 800)
#: Plus grande capture affichée dans la fenêtre de masquage.
REDACTION_LIMIT = (1100, 680)


def _load_preview(directory, step):
    with Image.open(capture_path(directory, step), formats=("PNG",)) as original:
        original.load()
        pixels = original if original.mode == "RGB" else original.convert("RGB")
        return preview_image(pixels, REVIEW_LIMIT)


class ImageView(QLabel):
    """Capture centrée, réduite à la place disponible, jamais agrandie."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        set_role(self, "preview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(120, 90)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._source: QPixmap | None = None
        self._shown_for = None

    @property
    def has_image(self) -> bool:
        return self._source is not None

    def set_image(self, image: Image.Image | None) -> None:
        self._source = to_pixmap(image) if image is not None else None
        self._shown_for = None
        self._render()

    def set_message(self, text: str) -> None:
        self._source = None
        self._shown_for = None
        self.clear()
        self.setText(text)

    def _render(self) -> None:
        if self._source is None:
            return
        area = self.contentsRect().size()
        width, height = max(1, area.width() - 12), max(1, area.height() - 12)
        if (width, height) == self._shown_for:
            return
        source = self._source
        if source.width() > width or source.height() > height:
            ratio = self.devicePixelRatioF()
            scaled = source.scaled(int(width * ratio), int(height * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
        else:
            scaled = source
        self.setPixmap(scaled)
        self._shown_for = (width, height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render()


class RedactionCanvas(QWidget):
    """Capture sur laquelle on trace des rectangles à la souris."""

    def __init__(self, pixmap: QPixmap, on_change):
        super().__init__()
        self._pixmap = pixmap
        self._on_change = on_change
        self.boxes: list[QRect] = []
        self._start: QPoint | None = None
        self._current: QRect | None = None
        self.setFixedSize(pixmap.size())
        self.setCursor(Qt.CursorShape.CrossCursor)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("black"))
        for box in self.boxes:
            painter.drawRect(box)
        if self._current is not None:
            painter.setBrush(QColor(0, 0, 0, 120))
            painter.setPen(QPen(QColor(PALETTE["record"]), 1))
            painter.drawRect(self._current)

    def press(self, point: QPoint) -> None:
        self._start = point
        self._current = QRect(point, point)
        self.update()

    def drag(self, point: QPoint) -> None:
        if self._start is not None:
            self._current = QRect(self._start, point).normalized()
            self.update()

    def release(self, point: QPoint) -> None:
        if self._start is None:
            return
        box = QRect(self._start, point).normalized()
        if box.width() >= 3 and box.height() >= 3:
            self.boxes.append(box)
        self._start = self._current = None
        self.update()
        self._on_change()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.press(event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.drag(event.position().toPoint())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.release(event.position().toPoint())


class RedactionDialog(QDialog):
    """Rectangles noirs tracés sur une capture ; renvoyés en pixels de l'image."""

    def __init__(self, parent: QWidget, image: Image.Image, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.result: list[tuple[int, int, int, int]] | None = None
        self._factor = min(1.0, REDACTION_LIMIT[0] / image.width, REDACTION_LIMIT[1] / image.height)
        shown = image.resize((max(1, round(image.width * self._factor)), max(1, round(image.height * self._factor))),
                             Image.Resampling.LANCZOS) if self._factor < 1 else image
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(set_role(QLabel("Masquer une zone"), "cardtitle"))
        hint = set_role(QLabel("Faites glisser la souris sur chaque zone à noircir. Le masquage est définitif : "
                               "les pixels d’origine ne sont pas conservés."), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.canvas = RedactionCanvas(to_pixmap(shown), self._update_buttons)
        layout.addWidget(self.canvas, alignment=Qt.AlignmentFlag.AlignCenter)
        buttons = QHBoxLayout()
        self.undo_button = QPushButton("Annuler la dernière zone")
        self.undo_button.clicked.connect(self._undo)
        buttons.addWidget(self.undo_button)
        buttons.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.apply_button = set_role(QPushButton("Masquer définitivement"), "primary")
        self.apply_button.clicked.connect(self._apply)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        self._update_buttons()

    @property
    def boxes(self) -> list[tuple[int, int, int, int]]:
        return [tuple(round(value / self._factor) for value in (box.left(), box.top(), box.right() + 1,
                                                                box.bottom() + 1)) for box in self.canvas.boxes]

    def _undo(self) -> None:
        if self.canvas.boxes:
            self.canvas.boxes.pop()
            self.canvas.update()
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.undo_button.setEnabled(bool(self.canvas.boxes))
        self.apply_button.setEnabled(bool(self.canvas.boxes))

    def _apply(self) -> None:
        self.result = self.boxes
        self.accept()


class _FocusOut(QObject):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.FocusOut:
            self._callback()
        return False


class SessionReview(QWidget):
    """Éditeur de guide : liste paginée des étapes, une capture et des textes modifiables."""

    def __init__(self, *, choose_session, on_loaded, on_error, on_changed=None, export=None, options=None):
        super().__init__()
        self._on_loaded, self._on_error = on_loaded, on_error
        self._on_changed = on_changed or (lambda _message: None)
        self._export = export
        # Préférences de l'application (confirmation, conservation des captures).
        self._app_options = options or (lambda: {"confirm_delete": True, "keep_retired": True})
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="StepWatch-relecture")
        self._running = None
        self._pending = None
        self._generation = 0
        self.directory = None
        self.name = ""
        self.steps = []
        self._page = 0
        self._selected: str | None = None
        self._image = None
        self._editable = True
        self._filling = False
        self._undo: list[tuple] = []
        self.guide_name = Value("")
        self.step_title_text = Value("")
        self.dialog_runner = lambda dialog: dialog.exec()  # Remplaçable dans les tests.

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)

        # En-tête du guide : titre modifiable et export, comme un document.
        header = set_role(QWidget(), "card")
        header_layout = QGridLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.addWidget(set_role(QLabel("TITRE DU GUIDE"), "section"), 0, 0)
        self.name_entry = set_role(self.guide_name.bind_line(QLineEdit()), "title")
        self.name_entry.setEnabled(False)
        self.name_entry.editingFinished.connect(self._commit_name)
        header_layout.addWidget(self.name_entry, 1, 0)
        self.title = set_role(QLabel("Aucun guide ouvert"), "muted")
        header_layout.addWidget(self.title, 2, 0)
        self.open_button = QPushButton("Ouvrir un guide")
        self.open_button.clicked.connect(choose_session)
        header_layout.addWidget(self.open_button, 0, 1, 3, 1)
        self.export_button = QToolButton()
        self.export_button.setText("Exporter le guide")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_menu = QMenu(self.export_button)
        for label, kind in (("Rapport autonome (HTML, un seul fichier)", "standalone"),
                            ("Markdown (rapport.md)", "markdown"),
                            ("Ouvrir le dossier du guide", "folder")):
            self.export_menu.addAction(label, lambda kind=kind: self._export and self._export(kind))
        self.export_button.setMenu(self.export_menu)
        self.export_button.setEnabled(False)
        header_layout.addWidget(self.export_button, 0, 2, 3, 1)
        header_layout.setColumnStretch(0, 1)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        listing = set_role(QWidget(), "card")
        listing_layout = QVBoxLayout(listing)
        listing_layout.setContentsMargins(12, 12, 12, 12)
        self.list = QTreeWidget()
        self.list.setColumnCount(3)
        self.list.setHeaderLabels(["N°", "Étape", "Type"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header_view = self.list.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setStretchLastSection(False)
        self.list.itemSelectionChanged.connect(self._select_step)
        listing_layout.addWidget(self.list, 1)
        pager = QHBoxLayout()
        self.previous = QPushButton("‹ Précédent")
        self.previous.clicked.connect(lambda: self._show_page(self._page - 1))
        self.page_label = set_role(QLabel(""), "muted")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next = QPushButton("Suivant ›")
        self.next.clicked.connect(lambda: self._show_page(self._page + 1))
        pager.addWidget(self.previous)
        pager.addWidget(self.page_label, 1)
        pager.addWidget(self.next)
        listing_layout.addLayout(pager)
        listing.setMinimumWidth(320)
        body.addWidget(listing, 3)

        detail = set_role(QWidget(), "card")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        top = QHBoxLayout()
        self.step_badge = set_role(QLabel(""), "badge")
        self.step_kind = set_role(QLabel(""), "chip")
        top.addWidget(self.step_badge)
        top.addWidget(self.step_kind)
        top.addStretch(1)
        self.undo_button = set_role(QPushButton("↶ Annuler"), "icon")
        self.undo_button.setToolTip("Annuler la dernière modification (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo)
        self.up_button = set_role(QPushButton("↑"), "icon")
        self.up_button.setToolTip("Monter l’étape (Ctrl+↑)")
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button = set_role(QPushButton("↓"), "icon")
        self.down_button.setToolTip("Descendre l’étape (Ctrl+↓)")
        self.down_button.clicked.connect(lambda: self._move(1))
        self.redact_button = set_role(QPushButton("Masquer une zone…"), "icon")
        self.redact_button.clicked.connect(self._redact)
        self.delete_button = set_role(QPushButton("Supprimer"), "icon")
        self.delete_button.setToolTip("Supprimer l’étape (Suppr)")
        self.delete_button.clicked.connect(self._delete)
        for button in (self.undo_button, self.up_button, self.down_button, self.redact_button, self.delete_button):
            top.addWidget(button)
        detail_layout.addLayout(top)
        self.step_time = set_role(QLabel(""), "muted")
        detail_layout.addWidget(self.step_time)
        self.title_hint = self.step_time  # Le titre par défaut figure sur la ligne de l'heure.
        self.title_entry = set_role(self.step_title_text.bind_line(QLineEdit()), "steptitle")
        self.title_entry.setPlaceholderText("Titre de l’étape")
        self.title_entry.setEnabled(False)
        self.title_entry.editingFinished.connect(self._commit_step)
        detail_layout.addWidget(self.title_entry)
        self.canvas = ImageView("Ouvrez un guide pour revoir et modifier ses étapes.")
        detail_layout.addWidget(self.canvas, 1)
        detail_layout.addWidget(set_role(QLabel("DESCRIPTION"), "section"))
        self.annotation = QPlainTextEdit()
        self.annotation.setPlaceholderText("Description de l’étape (facultative)")
        self.annotation.setFixedHeight(72)
        self.annotation.setEnabled(False)
        self._annotation_filter = _FocusOut(self._commit_step)
        self.annotation.installEventFilter(self._annotation_filter)
        detail_layout.addWidget(self.annotation)
        body.addWidget(detail, 6)

        self._show_page(0)
        self._update_tools()
        # Raccourcis de l'éditeur, actifs seulement quand l'onglet Guide est visible.
        self.shortcuts = {"Delete": self._delete, "Ctrl+Up": lambda: self._move(-1),
                          "Ctrl+Down": lambda: self._move(1), "Ctrl+Z": self.undo, "F2": self._focus_title}
        self._shortcut_objects = []
        for sequence, action in self.shortcuts.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda sequence=sequence, action=action: self._shortcut(sequence, action))
            self._shortcut_objects.append(shortcut)

    # ----- Chargement asynchrone -------------------------------------------------

    @property
    def busy(self):
        return self._running is not None or self._pending is not None

    @property
    def loading_session(self):
        """Un guide est en cours de lecture (le chargement d'un aperçu n'empêche pas d'éditer)."""
        return any(job is not None and job[1] == "session" for job in (self._running, self._pending))

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
        """Le guide affiché reste visible si la session demandée est invalide."""
        self._commit_step()
        self._request("session", directory)

    def clear(self):
        self._generation += 1
        self._pending = None
        self.directory = None
        self.name = ""
        self.steps = []
        self._selected = None
        self._undo.clear()
        self.guide_name.set("")
        self.title.setText("Aucun guide ouvert")
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
                    self._undo.clear()
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
                    self.canvas.set_image(result)
        self._update_tools()
        self._dispatch()

    # ----- Liste et sélection --------------------------------------------------

    def _update_count(self):
        count = len(self.steps)
        self.title.setText(f"{count} étape{'s' if count > 1 else ''} · enregistrement automatique · "
                           "Suppr supprimer · Ctrl+↑/↓ déplacer · Ctrl+Z annuler · F2 titre")

    def _row_values(self, step):
        kind = step_trigger_label(step)
        heading = step_heading(step)
        # La colonne Type n'apporte une information que si un titre a été écrit.
        return heading, ("" if heading == kind else kind)

    def _items(self) -> dict[str, QTreeWidgetItem]:
        return {self.list.topLevelItem(row).data(0, Qt.ItemDataRole.UserRole): self.list.topLevelItem(row)
                for row in range(self.list.topLevelItemCount())}

    def item(self, position: int) -> QTreeWidgetItem | None:
        return self._items().get(str(position))

    def _show_page(self, page, select=None):
        self._page = page
        self.list.blockSignals(True)
        try:
            self.list.clear()
            start = page * PAGE_SIZE
            end = min(start + PAGE_SIZE, len(self.steps))
            for position in range(start, end):
                step = self.steps[position]
                title, kind = self._row_values(step)
                item = QTreeWidgetItem([str(step["index"]), title, kind])
                item.setData(0, Qt.ItemDataRole.UserRole, str(position))
                self.list.addTopLevelItem(item)
        finally:
            self.list.blockSignals(False)
        self.page_label.setText(f"{start + 1}-{end} / {len(self.steps)}" if end else "0 étape")
        self.previous.setEnabled(bool(start))
        self.next.setEnabled(end < len(self.steps))
        target = select if select is not None and start <= select < end else (start if start < end else None)
        if target is not None:
            self._selected = None
            item = self.item(target)
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item)

    def select(self, position: int) -> None:
        """Sélectionne une étape (affiche sa page si besoin)."""
        if self.item(position) is None:
            self._show_page(position // PAGE_SIZE, select=position)
        else:
            self.list.setCurrentItem(self.item(position))

    def _set_annotation(self, text):
        self.annotation.setPlainText(text)

    def _fill_step(self, step):
        self._filling = True
        try:
            if step is None:
                self.step_badge.setText("")
                self.step_badge.setVisible(False)
                self.step_kind.setText("")
                self.step_kind.setVisible(False)
                self.step_time.setText("")
                self.step_title_text.set("")
                self._set_annotation("")
                return
            self.step_badge.setText(f"Étape {step['index']}")
            self.step_badge.setVisible(True)
            kind = step_trigger_label(step)
            self.step_kind.setText(kind)
            self.step_kind.setVisible(True)
            try:
                when = datetime.fromisoformat(step["timestamp"]).astimezone().strftime("%d/%m/%Y à %H:%M:%S")
            except (ValueError, OSError, OverflowError):
                when = step["timestamp"]
            self.step_time.setText(f"{when}  ·  titre de l’étape ci-dessous (vide : « {kind} »)")
            self.step_title_text.set(step.get("title") or "")
            self._set_annotation(step["annotation"] or "")
        finally:
            self._filling = False

    def _select_step(self):
        if self.loading_session:
            return
        current = self.list.currentItem()
        selected = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        if selected is None or selected == self._selected or self.directory is None:
            return
        self._commit_step()
        self._selected = selected
        step = self.steps[int(selected)]
        self._fill_step(step)
        self._clear_image("Chargement de la capture…")
        self._request("preview", self.directory, step)
        self._update_tools()

    def _clear_image(self, message):
        self._image = None
        self.canvas.set_message(message)

    # ----- Édition ---------------------------------------------------------------

    def set_editable(self, editable: bool):
        """L'application interdit les modifications pendant un enregistrement ou un traitement."""
        if editable != self._editable:
            if not editable:
                self._commit_step()
            self._editable = editable
            self._update_tools()

    def _can_edit(self):
        return self._editable and self.directory is not None and not self.loading_session

    def _position(self):
        return int(self._selected) if self._selected is not None and self._selected.isdigit() else None

    def _update_tools(self):
        editable = self._can_edit()
        position = self._position()
        has_step = editable and position is not None and position < len(self.steps)
        self.name_entry.setEnabled(editable)
        self.export_button.setEnabled(self.directory is not None and self._editable and not self.loading_session)
        self.title_entry.setEnabled(has_step)
        self.annotation.setEnabled(has_step)
        self.up_button.setEnabled(has_step and position > 0)
        self.down_button.setEnabled(has_step and position < len(self.steps) - 1)
        for button in (self.redact_button, self.delete_button):
            button.setEnabled(has_step)
        self.undo_button.setEnabled(editable and bool(self._undo))

    def _remember(self, entry):
        self._undo.append(entry)
        del self._undo[:-UNDO_LIMIT]

    def _save(self, steps, select=None, message=None, undoable=True):
        """Écrit le guide, relit les étapes enregistrées et garde la sélection."""
        before = ("save", self.name, [dict(step) for step in self.steps], self._position())
        try:
            deleted_standalone = save_guide(self.directory, self.guide_name.get(), steps)
            self.name, _, self.steps = read_session(self.directory)
        except (StorageError, OSError, ValueError) as exc:
            self._on_error(str(exc))
            return False
        if undoable:
            self._remember(before)
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

    def _commit_name(self):
        if self._can_edit() and " ".join(self.guide_name.get().split()) != self.name:
            self._save(list(self.steps), message="Titre du guide enregistré.")

    def _commit_step(self):
        """Enregistre le titre et la description de l'étape sélectionnée s'ils ont changé."""
        position = self._position()
        if self._filling or not self._can_edit() or position is None or position >= len(self.steps):
            return
        step = self.steps[position]
        title = " ".join(self.step_title_text.get().split())
        description = self.annotation.toPlainText().strip()
        if title == (step.get("title") or "") and description == (step["annotation"] or ""):
            return
        steps = [dict(item) for item in self.steps]
        steps[position].update(title=title, annotation=description)
        if self._save(steps):
            item = self.item(position)
            if item is not None:
                title_text, kind = self._row_values(self.steps[position])
                item.setText(1, title_text)
                item.setText(2, kind)

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
        options = self._app_options()
        keep = options.get("keep_retired", True)
        fate = (f"Sa capture est déplacée dans captures/{RETIRED_FOLDER}/ et n’apparaît plus dans les rapports."
                if keep else "Sa capture est supprimée définitivement : cette suppression ne pourra pas être annulée.")
        if (options.get("confirm_delete", True) or not keep) and not messagebox.askyesno(
                "Supprimer l’étape",
                f"Retirer l’étape {step['index']} (« {step_heading(step)} ») du guide ?\n\n{fate}",
                parent=self.window()):
            return
        before = [dict(item) for item in self.steps]
        name = self.name
        try:
            removal = remove_step(self.directory, self.guide_name.get(), list(self.steps), position, keep=keep)
            self.name, _, self.steps = read_session(self.directory)
        except (StorageError, OSError, ValueError) as exc:
            self._on_error(str(exc))
            return
        deleted_standalone = removal.deleted_standalone
        if removal.retired is not None:
            self._remember(("delete", name, before, position, removal.retired, step["image"]))
        if not self.steps:
            self._selected = None
            self._fill_step(None)
            self._clear_image("Ce guide ne contient plus aucune étape.")
            self._show_page(0)
        self._after_change(deleted_standalone, min(position, len(self.steps) - 1) if self.steps else None,
                           f"Étape {step['index']} retirée du guide (capture conservée dans captures/{RETIRED_FOLDER}/)."
                           if keep else f"Étape {step['index']} supprimée avec sa capture.")

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
        dialog = RedactionDialog(self.window(), image, f"Masquer une zone · étape {step['index']}")
        self.dialog_runner(dialog)
        result = dialog.result
        dialog.deleteLater()
        if not result:
            return
        try:
            deleted_standalone = redact_step(self.directory, self.guide_name.get(), list(self.steps),
                                             position, result)
            self.name, _, self.steps = read_session(self.directory)
        except (StorageError, OSError, ValueError) as exc:
            self._on_error(str(exc))
            return
        self._after_change(deleted_standalone, position,
                           f"Étape {step['index']} : {len(result)} zone(s) masquée(s) définitivement "
                           "(un masquage ne peut pas être annulé).")
        self._selected = None
        self._select_step()

    def undo(self):
        """Annule le dernier changement de titre, description, nom, ordre ou suppression."""
        self._commit_step()
        if not self._can_edit() or not self._undo:
            return
        entry = self._undo.pop()
        kind, name, steps, position = entry[:4]
        try:
            if kind == "delete":
                retired, image = entry[4], entry[5]
                deleted_standalone = restore_step(self.directory, name, steps, retired, image)
            else:
                deleted_standalone = save_guide(self.directory, name, steps)
            self.name, _, self.steps = read_session(self.directory)
        except (StorageError, OSError, ValueError) as exc:
            self._on_error(f"Annulation impossible : {exc}")
            self._update_tools()
            return
        select = position if position is not None and position < len(self.steps) else None
        self._selected = None
        self._after_change(deleted_standalone, select,
                           "Étape restaurée." if kind == "delete" else "Dernière modification annulée.")
        if select is not None:
            self._fill_step(self.steps[select])

    def _focus_title(self):
        if self.title_entry.isEnabled():
            self.title_entry.setFocus()
            self.title_entry.selectAll()

    def _shortcut(self, sequence: str, action):
        """Raccourci de l'éditeur, sauf onglet masqué ou saisie en cours dans un champ."""
        if not self.isVisible() or not self._can_edit():
            return False
        focused = QApplication.focusWidget()
        if isinstance(focused, _TEXT_WIDGETS) and sequence != "F2":
            return False  # Suppr, Ctrl+Z et Ctrl+flèches gardent leur sens dans un champ.
        action()
        return True

    def shutdown(self, timeout=0):
        self._pending = None
        if self._running is not None:
            # Attendre la fin sans lire le résultat : une erreur d'E/S du
            # traitement ne signifie pas qu'il tourne encore.
            if wait((self._running[3],), timeout=max(0, timeout)).not_done:
                return False
            self._running = None
        self._executor.shutdown(wait=True)
        return True
