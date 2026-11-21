"""Edit a finished session as a guide, after recording.

Rename the guide, give each step a title and a description, reorder steps,
remove a step, or black out an area forgotten in a capture. Each change
rewrites ``steps.json``, ``rapport.html`` and ``rapport.md`` atomically. An
existing ``rapport-autonome.html`` embeds the previous captures, possibly the
removed or unredacted ones: it is deleted, never left stale.

Never call these functions on a session that is still being recorded.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from PIL import Image, ImageDraw

from .storage import (MARKDOWN_REPORT, NOTE_MAX_LENGTH, PNG_COMPRESS_LEVEL, STANDALONE_REPORT,
                      TITLE_MAX_LENGTH, StorageError, _atomic_text, _markdown, _replace,
                      capture_path, html_report, read_session)

#: Captures of removed steps are moved here, outside every report, so that a
#: removal is never a silent destruction of evidence. Delete the folder to
#: discard them.
RETIRED_FOLDER = "retirees"
NAME_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 4 * NOTE_MAX_LENGTH


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def save_guide(directory: Path, name: str, steps: list[dict[str, Any]], *,
               restored: tuple[str, ...] = ()) -> bool:
    """Write the guide in this order and rebuild its reports.

    ``steps`` must reference captures of this session. Indexes are
    renumbered from 1. Returns True when a stale standalone report was
    deleted.
    """
    directory = Path(directory)
    _, started_at, current = read_session(directory)
    # ``restored``: a capture just moved back from captures/retirees/ (undo).
    known = {step["image"] for step in current} | set(restored)
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, step in enumerate(steps, start=1):
        image = str(step["image"])
        if image not in known or image in seen:
            raise StorageError(f"Étape {position} : capture inconnue ou en double ({image}).")
        seen.add(image)
        capture_path(directory, step)  # Refuse paths outside captures/.
        clean.append({
            "index": position,
            "timestamp": str(step["timestamp"]),
            "trigger": str(step["trigger"]),
            "image": image,
            "click": step.get("click"),
            "click_count": int(step.get("click_count", 0)),
            "annotation": str(step.get("annotation") or "").strip()[:DESCRIPTION_MAX_LENGTH],
            "title": _one_line(step.get("title"), TITLE_MAX_LENGTH),
        })
    name = _one_line(name, NAME_MAX_LENGTH) or "Intervention"
    metadata = {"format_version": 1, "application": "StepWatch", "name": name,
                "started_at": started_at, "steps": clean}
    try:
        _atomic_text(directory / "steps.json", json.dumps(metadata, ensure_ascii=False, separators=(",", ":")))
        _atomic_text(directory / "rapport.html", html_report(name, started_at, clean))
        _atomic_text(directory / MARKDOWN_REPORT, _markdown(name, started_at, clean))
        standalone = directory / STANDALONE_REPORT
        if standalone.exists():
            standalone.unlink()
            return True
    except OSError as exc:
        raise StorageError(f"Enregistrement du guide impossible dans {directory} : {exc}") from exc
    return False


class Removal(NamedTuple):
    deleted_standalone: bool
    retired: Path  # Where the capture now is, for restore_step().


def remove_step(directory: Path, name: str, steps: list[dict[str, Any]], position: int) -> Removal:
    """Remove ``steps[position]`` from the guide; its PNG moves to captures/retirees/.

    The guide is saved first: if moving the PNG then fails, the capture only
    stays unreferenced in captures/.
    """
    removed = steps[position]
    source = capture_path(Path(directory), removed)
    remaining = steps[:position] + steps[position + 1:]
    deleted_standalone = save_guide(directory, name, remaining)
    retired = source.parent / RETIRED_FOLDER
    try:
        retired.mkdir(mode=0o700, exist_ok=True)
        destination = retired / source.name
        suffix = 1
        while destination.exists():
            destination = retired / f"{source.stem}-{suffix}{source.suffix}"
            suffix += 1
        os.replace(source, destination)
    except OSError as exc:
        raise StorageError(f"Étape retirée du guide, mais capture non déplacée ({source.name}) : {exc}") from exc
    return Removal(deleted_standalone, destination)


def restore_step(directory: Path, name: str, steps: list[dict[str, Any]], retired: Path, image: str) -> bool:
    """Undo remove_step: put the retired PNG back at ``image``, then save ``steps``.

    ``steps`` is the guide as it was before the removal. Returns save_guide's
    result. Refuses a file outside captures/retirees/ or an occupied target.
    """
    directory = Path(directory)
    captures = (directory / "captures").resolve()
    retired = Path(retired)
    if retired.resolve().parent != captures / RETIRED_FOLDER or not retired.is_file():
        raise StorageError(f"Capture retirée introuvable : {retired.name}.")
    target = directory / image
    if target.resolve().parent != captures or Path(image).suffix.lower() != ".png":
        raise StorageError(f"Emplacement de capture refusé : {image}.")
    if target.exists():
        raise StorageError(f"Une capture occupe déjà {image} : restauration impossible.")
    try:
        os.replace(retired, target)
    except OSError as exc:
        raise StorageError(f"Restauration de {retired.name} impossible : {exc}") from exc
    return save_guide(directory, name, steps, restored=(image,))


def redact_step(directory: Path, name: str, steps: list[dict[str, Any]], position: int,
                boxes: list[tuple[int, int, int, int]]) -> bool:
    """Black out ``boxes`` (left, top, right, bottom, image pixels) in a capture.

    The PNG is replaced atomically: the original pixels are not kept anywhere
    in the session. Returns save_guide's result (stale standalone deleted).
    """
    directory = Path(directory)
    path = capture_path(directory, steps[position])
    try:
        with Image.open(path, formats=("PNG",)) as original:
            image = original.convert("RGB")
    except OSError as exc:
        raise StorageError(f"Capture illisible ({path.name}) : {exc}") from exc
    drawing = ImageDraw.Draw(image)
    drawn = 0
    for box in boxes:
        left, top, right, bottom = (int(value) for value in box)
        left, right = sorted((max(0, min(left, image.width)), max(0, min(right, image.width))))
        top, bottom = sorted((max(0, min(top, image.height)), max(0, min(bottom, image.height))))
        if right - left >= 1 and bottom - top >= 1:
            drawing.rectangle((left, top, right - 1, bottom - 1), fill="black")
            drawn += 1
    if not drawn:
        raise ValueError("La zone à masquer est vide.")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".stepwatch-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            image.save(handle, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temporary, path)
    except OSError as exc:
        raise StorageError(f"Masquage de {path.name} impossible : {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return save_guide(directory, name, steps)
