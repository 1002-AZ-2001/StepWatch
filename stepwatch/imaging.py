"""Pure image processing used by capture workers and offline tests."""

from __future__ import annotations

import math
from typing import Iterable, TYPE_CHECKING

from PIL import Image, ImageChops, ImageDraw, ImageFilter

if TYPE_CHECKING:
    from .models import Rect

#: Normal-mode comparisons run on at most about this many pixels. A 3x3 median
#: on a full 1080p frame costs 0.2-0.9 s per image with Pillow; box reduction
#: first bounds the cost and is itself a low-pass filter against ClearType
#: fringes and dithering. Conservative mode keeps full resolution.
ANALYSIS_MAX_PIXELS = 500_000


def analysis_factor(size: tuple[int, int], max_pixels: int = ANALYSIS_MAX_PIXELS) -> int:
    """Integer box-reduction factor keeping the analysed area near max_pixels."""
    width, height = size
    if width * height <= max_pixels:
        return 1
    return math.ceil(math.sqrt(width * height / max_pixels))


def apply_masks(image: Image.Image, masks: Iterable[Rect]) -> Image.Image:
    """Return an RGB copy with target-relative sensitive rectangles blacked out.

    Masking must happen *before* comparison, previews and writing to disk. Masks
    outside a resized target are clipped, rather than exposing an unmasked copy.
    """
    result = image.convert("RGB")  # Always a new image, even for RGB input.
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
    result = image.convert("RGB")
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

    Normal mode smooths subpixel flicker and removes isolated one-pixel noise;
    large targets are first box-reduced (see ``ANALYSIS_MAX_PIXELS``) so that a
    comparison stays well below the sampling interval on 1080p/4K screens.
    Conservative mode keeps full resolution and smaller, lower-contrast
    changes. This is an area measurement, not an inference about remote
    mouse/keyboard activity. A small cursor or blinking caret usually affects
    much less area than a real step; the recorder applies the user's
    sensitivity threshold to this percentage.
    """
    if previous is None or previous.size != current.size:
        return 100.0
    before = previous if previous.mode == "RGB" else previous.convert("RGB")
    after = current if current.mode == "RGB" else current.convert("RGB")
    if not conservative:
        factor = analysis_factor(after.size)
        if factor > 1:
            before, after = before.reduce(factor), after.reduce(factor)
        before = before.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.65))
        after = after.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.65))
    diff = ImageChops.difference(before, after)
    red, green, blue = diff.split()
    largest_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    threshold = 8 if conservative else 22
    changed = largest_channel.point(lambda value: 255 if value >= threshold else 0)
    if not conservative:
        changed = changed.filter(ImageFilter.MedianFilter(3))
    # The proportion is measured on the analysed (possibly reduced) image.
    pixels = changed.width * changed.height
    if pixels == 0:
        return 0.0
    return 100.0 * changed.histogram()[255] / pixels
