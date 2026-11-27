"""``stepwatch --self-test``: check an installed package without user data.

Exercises the image, storage and report code on a temporary synthetic
session, then opens and closes the real interface when a display is
available. No network access, no screen capture and no file outside a
temporary folder (except the usual diagnostic log).
"""

from __future__ import annotations

import gc
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from . import __version__
from .display import display_problem, session_type
from .imaging import apply_masks, changed_percent, mark_click
from .models import Rect
from .storage import MARKDOWN_REPORT, STANDALONE_REPORT, SessionStore, export_standalone, import_sharex

LOGGER = logging.getLogger(__name__)


def _synthetic(step: int) -> Image.Image:
    image = Image.new("RGB", (320, 200), "#dfe6ee")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((20, 20, 300, 180), fill="white", outline="#5b6b7d")
    drawing.rectangle((40, 40 + 30 * step, 200, 60 + 30 * step), fill="#1f6feb")
    return image


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check_reports(base: Path) -> None:
    """Session with masks, clicks, a note, every report and a ShareX import."""
    store = SessionStore(base, "Auto-test StepWatch")
    previous = None
    mask = (Rect(250, 150, 40, 20),)
    for step in range(3):
        clean = apply_masks(_synthetic(step), mask)
        if previous is not None:
            _check(changed_percent(previous, clean) > 0, "comparaison visuelle sans effet")
        output = apply_masks(mark_click(clean, (120, 50 + 30 * step)), mask)
        _check(output.getpixel((260, 160)) == (0, 0, 0), "masque non appliqué")
        store.save(output, "local_click", click=(120, 50 + 30 * step), annotation="Clic gauche local.")
        previous = clean
    store.add_note("Note d'auto-test")
    store.close()
    for name in ("steps.json", "rapport.html", MARKDOWN_REPORT):
        _check((store.directory / name).is_file(), f"{name} absent")
    _check("Note : Note d'auto" in (store.directory / MARKDOWN_REPORT).read_text(encoding="utf-8"),
           "note absente du rapport Markdown")
    standalone = export_standalone(store.directory)
    _check(standalone.name == STANDALONE_REPORT and standalone.stat().st_size > 0,
           "rapport autonome vide")
    imported = import_sharex(store.directory / "captures", base, name="Auto-test import")
    _check(len(list((imported / "captures").glob("*.png"))) == 3, "import ShareX incomplet")


def check_sync(base: Path) -> None:
    """Synchronisation d'un guide de test vers un dossier local ; modules WebDAV et SFTP chargés."""
    from . import sync

    store = SessionStore(base, "Auto-test synchronisation")
    store.save(Image.new("RGB", (32, 24), "white"), "initial")
    store.close()
    destination = base / "serveur"
    destination.mkdir()
    result = sync.sync_guide(store.directory, sync.FolderTarget(destination))
    _check(result.sent >= 3 and (destination / store.directory.name / "steps.json").is_file(),
           "synchronisation vers un dossier incomplète")
    import paramiko  # noqa: F401 - SFTP disponible dans le paquet
    import keyring  # noqa: F401 - coffre du système disponible dans le paquet


def check_interface(*, capture_x11: bool = False) -> None:
    """Ouvre la vraie fenêtre principale, la laisse se dessiner, puis la ferme."""
    from .qtcompat import application, process_events
    from .ui import StepWatchApp

    application()
    app = StepWatchApp()
    try:
        app.root.show()
        for _ in range(10):
            process_events()
            time.sleep(0.01)
        _check(app.root.isVisible() and app.root.width() > 1, "fenêtre non dessinée")
        if capture_x11:
            from .x11 import X11Backend

            backend = X11Backend()
            try:
                # Lire une petite partie de la fenêtre de test, sans l'enregistrer
                # ni démarrer d'écoute. Une session Wayland n'ouvre jamais le portail ici.
                origin = app.root.mapToGlobal(app.root.rect().topLeft())
                image = backend.grab(Rect(origin.x(), origin.y(), 24, 24))
                _check(image.mode == "RGB" and image.size == (24, 24), "capture X11 invalide")
            finally:
                backend.close()
        _check(app.shutdown(timeout=2), "arrêt de l'interface incomplet")
    finally:
        app.dispose()
        process_events(3)
        app = None
        gc.collect()


def run(write: Callable[[str], None] | None = None) -> int:
    """0 when every check passes, 1 otherwise. Messages go to ``write``."""
    if write is None:
        def write(message: str) -> None:
            LOGGER.info(message)
            if sys.stdout is not None:  # None in the windowed Windows build.
                print(message, flush=True)

    session = session_type()
    write(f"StepWatch {__version__} — auto-test ({sys.platform}, session {session})")
    try:
        with tempfile.TemporaryDirectory(prefix="stepwatch-selftest-") as temporary:
            check_reports(Path(temporary))
        write("Images, rapports, notes et import ShareX : OK")
        with tempfile.TemporaryDirectory(prefix="stepwatch-selftest-sync-") as temporary:
            check_sync(Path(temporary))
        write("Synchronisation (dossier, WebDAV, SFTP) : OK")
        problem = display_problem()
        if problem:
            write(f"Interface Qt : non vérifiée — {problem}")
        else:
            capture_x11 = sys.platform.startswith("linux") and session == "x11"
            check_interface(capture_x11=capture_x11)
            write("Interface Qt : OK")
            if capture_x11:
                write("Capture X11 : OK")
    except Exception as exc:
        LOGGER.exception("Auto-test en échec")
        write(f"Auto-test en échec : {exc}")
        return 1
    write("Auto-test réussi.")
    return 0
