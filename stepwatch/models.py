"""Value objects shared by the interface, capture engine and Windows backend.

All rectangles use physical pixels. A target rectangle uses desktop coordinates
(which can be negative); a privacy mask uses coordinates relative to the target.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from numbers import Integral


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, Integral)
               for value in (self.left, self.top, self.width, self.height)):
            raise ValueError("Les coordonnées et dimensions doivent être des entiers.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("La largeur et la hauteur doivent être positives.")

    @classmethod
    def from_any(cls, value: object) -> "Rect":
        """Accept both dictionary and attribute based monitor structures.

        In particular, ``mss.monitors`` normally contains dictionaries. Avoid
        assuming attribute access (the historic StepWatch 1.0 error).
        """
        if isinstance(value, cls):
            return value
        names = ("left", "top", "width", "height")
        try:
            values = ([value[key] for key in names] if isinstance(value, Mapping)
                      else [getattr(value, key) for key in names])
            return cls(*values)
        except (KeyError, AttributeError, TypeError) as exc:
            raise ValueError("Rectangle incomplet : left, top, width et height requis.") from exc

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_dict(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}


@dataclass(frozen=True)
class CaptureTarget:
    kind: str
    label: str
    rect: Rect | None = None
    hwnd: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"window", "monitor", "region"}:
            raise ValueError("Type de cible inconnu.")
        if self.kind == "window" and (not isinstance(self.hwnd, int) or self.hwnd <= 0):
            raise ValueError("Une fenêtre doit posséder un identifiant Windows valide.")
        if self.kind != "window" and not isinstance(self.rect, Rect):
            raise ValueError("Un moniteur ou une zone doit posséder un rectangle valide.")


@dataclass(frozen=True)
class CaptureConfig:
    mode: str = "hybrid"
    interval: float = 1.0
    click_delay: float = 0.35
    sensitivity: float = 2.0
    conservative: bool = False
    masks: tuple[Rect, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"hybrid", "clicks", "visual"}:
            raise ValueError("Mode de capture inconnu.")
        for name, value, minimum, maximum in (
            ("L'intervalle", self.interval, 0.1, 3600),
            ("Le délai après clic", self.click_delay, 0.0, 60),
            ("Le seuil de changement", self.sensitivity, 0.01, 100),
        ):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not minimum <= value <= maximum):
                raise ValueError(f"{name} doit être compris entre {minimum} et {maximum}.")
        if not isinstance(self.conservative, bool):
            raise ValueError("Le mode conservateur doit être booléen.")
        if any(not isinstance(mask, Rect) for mask in self.masks):
            raise ValueError("Les masques doivent être des rectangles.")
        object.__setattr__(self, "masks", tuple(self.masks))
