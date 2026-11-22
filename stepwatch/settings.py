"""User preferences kept between launches (settings.json).

Stored locally only: %APPDATA%\\StepWatch\\settings.json on Windows,
$XDG_CONFIG_HOME/stepwatch/settings.json (~/.config) elsewhere. The file
holds folder paths and mask rectangles, never captures. A missing, invalid or
unwritable file never prevents StepWatch from starting: defaults are used.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
FILE_NAME = "settings.json"
RECENT_LIMIT = 10
MASKS_MAX_LENGTH = 10_000
_GEOMETRY = re.compile(r"\d{2,5}x\d{2,5}[+-]-?\d{1,5}[+-]-?\d{1,5}")

#: Each preference: default value and accepted type.
DEFAULTS: dict[str, Any] = {
    "output": "",
    "source": "",
    "mode": "",
    "interval": "1,0",
    "delay": "0,35",
    "sensitivity": "2,0",
    "conservative": False,
    "compact": True,
    "masks": "",
    "advanced_open": False,
    "geometry": "",
    "recent": [],
    # Fenêtre Paramètres.
    "name_template": "Nouvelle intervention",
    "bar_position": "bas",
    "global_hotkeys": True,
    "sound": False,
    "open_guide_after_stop": True,
    "confirm_delete": True,
    "keep_retired": True,
    "auto_standalone": False,
    "font_scale": 100,
    "theme": "systeme",
    "tray_icon": True,
    # Mises à jour (Paramètres › Général, menu Aide).
    "check_updates": True,
    "last_update_check": "",
    "skipped_version": "",
}

#: Allowed values of the preferences that are a choice.
CHOICES: dict[str, tuple] = {
    "bar_position": ("bas", "haut"),
    "font_scale": (90, 100, 115, 130),
    "theme": ("systeme", "clair", "sombre"),
}


def expand_name(template: str, now=None) -> str:
    """Guide name from a template: {date} (JJ/MM/AAAA) and {heure} (HH:MM)."""
    from datetime import datetime

    now = now or datetime.now()
    try:
        name = template.format(date=now.strftime("%d/%m/%Y"), heure=now.strftime("%H:%M"))
    except (KeyError, IndexError, ValueError):
        name = template  # Unknown placeholder or stray brace: keep the text as typed.
    return " ".join(name.split())[:200] or "Nouvelle intervention"


def default_path(platform: str | None = None, environ=None) -> Path:
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "StepWatch" / FILE_NAME
    base = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "stepwatch" / FILE_NAME


def _clean(raw: Any) -> dict[str, Any]:
    """Keep known keys with the expected type; anything else falls back to defaults."""
    values = {key: (list(value) if isinstance(value, list) else value) for key, value in DEFAULTS.items()}
    if not isinstance(raw, dict):
        return values
    for key, default in DEFAULTS.items():
        value = raw.get(key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                values[key] = value
        elif isinstance(default, int):
            if isinstance(value, int) and not isinstance(value, bool) and value in CHOICES.get(key, (value,)):
                values[key] = value
        elif isinstance(default, str):
            if isinstance(value, str) and len(value) <= (MASKS_MAX_LENGTH if key == "masks" else 1000):
                values[key] = value
        elif isinstance(default, list) and isinstance(value, list):
            values[key] = [item for item in value if isinstance(item, str) and 0 < len(item) <= 1000][:RECENT_LIMIT]
    if values["geometry"] and not _GEOMETRY.fullmatch(values["geometry"]):
        values["geometry"] = ""
    for key, allowed in CHOICES.items():
        if values[key] not in allowed:
            values[key] = DEFAULTS[key]
    return values


class Preferences:
    """Load once, change ``values``, then ``save()``."""

    def __init__(self, path: Path | None = None, *, persistent: bool = True):
        self.persistent = persistent
        self.path = Path(path) if path is not None else default_path()
        self.values = self._load() if persistent else _clean(None)

    @classmethod
    def memory(cls) -> "Preferences":
        """Defaults that are never read from or written to disk (tests, self-test)."""
        return cls(persistent=False)

    def reset(self) -> None:
        """Back to defaults, keeping the recent guides list."""
        recent = list(self.values["recent"])
        self.values = _clean(None)
        self.values["recent"] = recent

    def _load(self) -> dict[str, Any]:
        try:
            return _clean(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return _clean(None)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Préférences illisibles (%s) : valeurs par défaut utilisées.", exc)
            return _clean(None)

    def remember_guide(self, directory: Path) -> None:
        """Most recent first, without duplicates."""
        entry = str(Path(directory))
        self.values["recent"] = [entry] + [item for item in self.values["recent"] if item != entry]
        del self.values["recent"][RECENT_LIMIT:]

    def save(self) -> bool:
        if not self.persistent:
            return True
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".settings-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(_clean(self.values), handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
            return True
        except OSError as exc:
            LOGGER.warning("Préférences non enregistrées : %s", exc)
            return False
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
