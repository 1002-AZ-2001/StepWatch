"""Graphical session checks before Qt starts.

StepWatch draws with Qt: natively under X11 and Wayland. Under Wayland it
prefers XWayland when available, so that the floating recording bar can stay
on top at a chosen position (see qtcompat.choose_platform). Without any
display, Qt would only print a terse plugin error that a desktop launcher
hides: explain instead.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

#: Nom de l'application Qt : classe X11 (WM_CLASS « stepwatch », « StepWatch »)
#: et identifiant Wayland (app_id « stepwatch », nom du fichier .desktop).
#: Le fichier .desktop déclare StartupWMClass=stepwatch pour que les docks
#: (GNOME, KDE) associent la fenêtre à l'icône de StepWatch.
WINDOW_CLASS = "stepwatch"


def session_type(environ: Mapping[str, str] | None = None) -> str:
    """'wayland', 'x11' or 'inconnue', from the session environment."""
    environ = os.environ if environ is None else environ
    if environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    declared = environ.get("XDG_SESSION_TYPE", "").lower()
    if declared in {"wayland", "x11"}:
        return declared
    if environ.get("DISPLAY"):
        return "x11"
    return "inconnue"


def display_problem(platform: str | None = None,
                    environ: Mapping[str, str] | None = None) -> str | None:
    """Raison, en français, pour laquelle aucune fenêtre ne peut s'ouvrir, ou None."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform in {"win32", "darwin"} or environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"):
        return None
    if session_type(environ) == "wayland":
        return ("Session Wayland sans accès à l'affichage (WAYLAND_DISPLAY absent) : lancez StepWatch "
                "depuis le bureau, pas depuis une console distante ou un service.")
    return ("Aucun affichage graphique : lancez StepWatch depuis une session de bureau "
            "(X11 ou Wayland).")
