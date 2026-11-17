"""Pure image processing used by capture workers and offline tests."""

from __future__ import annotations

from dataclasses import dataclass, field
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
_NORMAL_THRESHOLD = tuple(255 if value >= 22 else 0 for value in range(256))
_CONSERVATIVE_THRESHOLD = tuple(255 if value >= 8 else 0 for value in range(256))


def analysis_factor(size: tuple[int, int], max_pixels: int = ANALYSIS_MAX_PIXELS) -> int:
    """Integer box-reduction factor keeping the analysed area near max_pixels."""
    width, height = size
    if width * height <= max_pixels:
        return 1
    factor = math.ceil(math.sqrt(width * height / max_pixels))
    # Account for rounding up in Image.reduce(), even on very narrow regions.
    while math.ceil(width / factor) * math.ceil(height / factor) > max_pixels:
        factor += 1
    return factor


def paint_masks(image: Image.Image, masks: Iterable[Rect]) -> None:
    """Redact an owned RGB image in place."""
    for mask in masks:
        left, top = max(0, mask.left), max(0, mask.top)
        right = min(image.width, mask.left + mask.width)
        bottom = min(image.height, mask.top + mask.height)
        if left < right and top < bottom:
            image.paste((0, 0, 0), (left, top, right, bottom))


def apply_masks(image: Image.Image, masks: Iterable[Rect]) -> Image.Image:
    """Return an RGB copy with target-relative sensitive rectangles blacked out.

    Masking must happen *before* comparison, previews and writing to disk. Masks
    outside a resized target are clipped, rather than exposing an unmasked copy.
    """
    result = image.convert("RGB")  # Always a new image, even for RGB input.
    paint_masks(result, masks)
    return result


def paint_click(image: Image.Image, click: tuple[int, int]) -> None:
    """Draw the local-click marker on an owned image."""
    x, y = click
    if not (0 <= x < image.width and 0 <= y < image.height):
        return
    drawing = ImageDraw.Draw(image)
    radius = 14
    bounds = (x - radius, y - radius, x + radius, y + radius)
    drawing.ellipse(bounds, outline="white", width=5)
    drawing.ellipse(bounds, outline="#e34435", width=3)
    drawing.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#e34435")


def mark_click(image: Image.Image, click: tuple[int, int] | None) -> Image.Image:
    """Add a visible local-click ring without modifying the source image."""
    result = image.convert("RGB")
    if click is not None:
        paint_click(result, click)
    return result


def preview_image(image: Image.Image, limit: tuple[int, int]) -> Image.Image:
    """Scale down without copying the full image or enlarging a small capture."""
    scale = min(1.0, limit[0] / image.width, limit[1] / image.height)
    if scale == 1.0:
        return image
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.BILINEAR, reducing_gap=2.0)


@dataclass(slots=True)
class VisualSample:
    size: tuple[int, int]
    pixels: Image.Image
    _filtered: Image.Image | None = field(default=None, init=False)

    def filtered(self) -> Image.Image:
        if self._filtered is None:
            self._filtered = self.pixels.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.65))
        return self._filtered


class VisualComparator:
    """Compare with the last kept frame, reusing its reduced and filtered pixels.

    Inputs and accepted samples are treated as read-only. The regular mode
    retains only the analysis resolution. Conservative mode uses full-resolution
    pixels but counts differences in strips to bound scratch memory.
    """

    def __init__(self, conservative: bool = False):
        self.conservative = conservative
        self.reference: VisualSample | None = None

    def prepare(self, image: Image.Image) -> VisualSample:
        pixels = image if image.mode == "RGB" else image.convert("RGB")
        if not self.conservative:
            factor = analysis_factor(image.size)
            if factor > 1:
                pixels = pixels.reduce(factor)
        return VisualSample(image.size, pixels)

    def compare(self, sample: VisualSample) -> float:
        reference = self.reference
        if reference is None or reference.size != sample.size:
            return 100.0
        before, after = reference.pixels, sample.pixels
        pixels = after.width * after.height
        if not pixels:
            return 0.0
        if self.conservative:
            rows = max(1, ANALYSIS_MAX_PIXELS // after.width)
            changed = 0
            for top in range(0, after.height, rows):
                if rows >= after.height:
                    diff = ImageChops.difference(before, after)
                else:
                    box = (0, top, after.width, min(top + rows, after.height))
                    diff = ImageChops.difference(before.crop(box), after.crop(box))
                if diff.getbbox():
                    changed += _changed_mask(diff, _CONSERVATIVE_THRESHOLD).histogram()[255]
            return 100.0 * changed / pixels

        if ImageChops.difference(before, after).getbbox() is None:
            return 0.0  # Static desktop: no median or Gaussian filters needed.
        diff = ImageChops.difference(reference.filtered(), sample.filtered())
        mask = _changed_mask(diff, _NORMAL_THRESHOLD).filter(ImageFilter.MedianFilter(3))
        return 100.0 * mask.histogram()[255] / pixels


def _changed_mask(diff: Image.Image, threshold: tuple[int, ...]) -> Image.Image:
    red, green, blue = diff.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue).point(threshold)


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
    comparator = VisualComparator(conservative)
    comparator.reference = comparator.prepare(previous)
    return comparator.compare(comparator.prepare(current))
