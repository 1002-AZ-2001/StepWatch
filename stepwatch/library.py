"""Find recent guides: folders opened before and sessions in the guides folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

SCAN_LIMIT = 500  # Folders examined in the guides folder, at most.


class GuideEntry(NamedTuple):
    directory: Path
    name: str
    modified: float


def _entry(directory: Path) -> GuideEntry | None:
    steps = directory / "steps.json"
    try:
        if directory.is_symlink() or not steps.is_file():
            return None
        modified = steps.stat().st_mtime
        try:
            name = str(json.loads(steps.read_text(encoding="utf-8")).get("name") or directory.name)
        except (ValueError, AttributeError):
            name = directory.name
        return GuideEntry(directory, " ".join(name.split())[:120] or directory.name, modified)
    except OSError:
        return None


def recent_guides(output: Path | str | None, remembered=(), limit: int = 10) -> list[GuideEntry]:
    """Most recently modified first; unreadable or vanished folders are skipped."""
    candidates: dict[str, Path] = {}
    for item in remembered:
        candidates.setdefault(str(Path(item)), Path(item))
    if output:
        base = Path(output)
        try:
            for index, child in enumerate(base.iterdir()):
                if index >= SCAN_LIMIT:
                    break
                if child.is_dir():
                    candidates.setdefault(str(child), child)
        except OSError:
            pass
    entries = [entry for entry in map(_entry, candidates.values()) if entry is not None]
    entries.sort(key=lambda entry: entry.modified, reverse=True)
    return entries[:limit]
