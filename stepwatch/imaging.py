"""Pure image processing used by capture workers and offline tests."""

from __future__ import annotations

from typing import Iterable, TYPE_CHECKING

from PIL import Image, ImageChops, ImageDraw, ImageFilter

if TYPE_CHECKING:
    from .models import Rect


def apply_masks(image: Image.Image, masks: Iterable[Rect]) -> Image.Image:
    """Return an RGB copy with target-relative sensitive rectangles blacked out.

    Masking must happen *before* comparison, previews and writing to disk. Masks
    outside a resized target are clipped, rather than exposing an unmasked copy.
    """
    result = image.convert("RGB").copy()
    drawing = ImageDraw.Draw(result)
    for mask in masks:
        left, top = max(0, mask.left), max(0, mask.top)
        right = min(result.width, mask.left + mask.width)
        bottom = min(result.height, mask.top + mask.height)
        if left < right and top < bottom:
            drawing.rectangle((left, top, right - 1, bottom - 1), fill="black")
    return result


def mark_click(image: Image.Image, click: tuple[int, int] | None) -> Image.Image:
    """Add a visible local-click ring without modifying the source image."""
    result = image.convert("RGB").copy()
    if click is None:
        return result
    x, y = click
    if not (0 <= x < result.width and 0 <= y < result.height):
        return result
    drawing = ImageDraw.Draw(result)
    radius = 14
    bounds = (x - radius, y - radius, x + radius, y + radius)
    drawing.ellipse(bounds, outline="white", width=5)
    drawing.ellipse(bounds, outline="#e34435", width=3)
    drawing.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#e34435")
    return result


def changed_percent(
    previous: Image.Image | None,
    current: Image.Image,
    conservative: bool = False,
) -> float:
    """Return the proportion of visibly changed pixels, between 0 and 100.

    Normal mode smooths subpixel flicker and removes isolated one-pixel noise.
    Conservative mode keeps smaller, lower-contrast changes. This is an area
    measurement, not an inference about remote mouse/keyboard activity. A small
    cursor or blinking caret usually affects much less area than a real step;
    the recorder applies the user's sensitivity threshold to this percentage.
    """
    if previous is None or previous.size != current.size:
        return 100.0
    before, after = previous.convert("RGB"), current.convert("RGB")
    if not conservative:
        before = before.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.65))
        after = after.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.65))
    diff = ImageChops.difference(before, after)
    red, green, blue = diff.split()
    largest_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    threshold = 8 if conservative else 22
    changed = largest_channel.point(lambda value: 255 if value >= threshold else 0)
    if not conservative:
        changed = changed.filter(ImageFilter.MedianFilter(3))
    pixels = current.width * current.height
    if pixels == 0:
        return 0.0
    return 100.0 * changed.histogram()[255] / pixels
