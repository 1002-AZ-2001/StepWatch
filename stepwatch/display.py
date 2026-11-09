"""Graphical session checks before Tk starts.

Tk has no native Wayland backend: under Wayland it draws through XWayland,
which compositors expose as an X11 ``DISPLAY``. Without it, Tk only raises
"no display name and no $DISPLAY environment variable", which a desktop
launcher hides. Explain what to install instead.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

#: X11 WM_CLASS instance name; Tk derives the class "Stepwatch" from it. The
#: .desktop file declares StartupWMClass=Stepwatch so that docks and task bars
#: (GNOME, KDE, also under XWayland) show the StepWatch icon, not "Tk".
WINDOW_CLASS = "stepwatch"


def session_type(environ: Mapping[str, str] | None = None) -> str:
    """'wayland', 'x11' or 'inconnue', from the session environment."""
    environ = os.environ if environ is None else environ
    declared = environ.get("XDG_SESSION_TYPE", "").lower()
    if declared in {"wayland", "x11"}:
        return declared
    if environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if environ.get("DISPLAY"):
        return "x11"
    return "inconnue"


def display_problem(platform: str | None = None,
                    environ: Mapping[str, str] | None = None) -> str | None:
    """French, actionable reason why Tk cannot open a window, or None."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform in {"win32", "darwin"} or environ.get("DISPLAY"):
        return None
    if environ.get("WAYLAND_DISPLAY"):
        return ("Session Wayland sans XWayland : StepWatch utilise Tk, qui s'affiche sous Wayland "
                "par l'intermédiaire de XWayland. Installez XWayland (Debian et Ubuntu : paquet "
                "« xwayland » ; Arch Linux : paquet « xorg-xwayland »), puis rouvrez votre session.")
    return ("Aucun affichage graphique : lancez StepWatch depuis une session de bureau "
            "(X11, ou Wayland avec XWayland).")
