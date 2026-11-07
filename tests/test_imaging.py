import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from stepwatch.imaging import (ANALYSIS_MAX_PIXELS, analysis_factor, apply_masks,
                               changed_percent, mark_click)


class ImagingTests(unittest.TestCase):
    def test_identical_images_have_no_change(self):
        image = Image.new("RGB", (100, 80), "#526b83")
        self.assertEqual(changed_percent(image, image.copy()), 0)

    def test_initial_and_resized_images_always_change(self):
        image = Image.new("RGB", (100, 80))
        self.assertEqual(changed_percent(None, image), 100)
        self.assertEqual(changed_percent(image, Image.new("RGB", (99, 80))), 100)

    def test_large_change_is_measured_as_area(self):
        before = Image.new("RGB", (100, 100), "white")
        after = before.copy()
        ImageDraw.Draw(after).rectangle((0, 0, 49, 99), fill="black")
        self.assertAlmostEqual(changed_percent(before, after), 50, delta=2)

    def test_differences_in_one_color_channel_are_detected(self):
        before = Image.new("RGB", (20, 20), (0, 0, 0))
        after = Image.new("RGB", (20, 20), (40, 0, 0))
        self.assertEqual(changed_percent(before, after), 100)

    def test_tiny_variations_are_ignored(self):
        before = Image.new("RGB", (100, 100), (60, 60, 60))
        after = Image.new("RGB", (100, 100), (65, 65, 65))
        self.assertEqual(changed_percent(before, after), 0)
        self.assertEqual(changed_percent(before, after, conservative=True), 0)

    def test_conservative_mode_keeps_lower_contrast_changes(self):
        before = Image.new("RGB", (100, 100), (60, 60, 60))
        after = Image.new("RGB", (100, 100), (75, 75, 75))
        self.assertEqual(changed_percent(before, after), 0)
        self.assertEqual(changed_percent(before, after, conservative=True), 100)

    def test_isolated_noise_is_removed_and_cursor_is_small(self):
        before = Image.new("RGB", (400, 300), "white")
        after = before.copy()
        after.putpixel((20, 20), (0, 0, 0))
        self.assertEqual(changed_percent(before, after), 0)
        ImageDraw.Draw(after).polygon(((50, 50), (50, 65), (60, 60)), fill="black")
        self.assertLess(changed_percent(before, after), 0.2)

    def test_large_targets_are_analysed_on_a_bounded_number_of_pixels(self):
        self.assertEqual(analysis_factor((400, 300)), 1)
        for size in ((1366, 768), (1920, 1080), (2560, 1440), (3840, 2160)):
            factor = analysis_factor(size)
            analysed = math.ceil(size[0] / factor) * math.ceil(size[1] / factor)
            with self.subTest(size=size):
                self.assertGreater(factor, 1)
                self.assertLessEqual(analysed, ANALYSIS_MAX_PIXELS)
        filtered_sizes = []
        original_filter = Image.Image.filter

        def spy(image, *args, **kwargs):
            filtered_sizes.append(image.size)
            return original_filter(image, *args, **kwargs)

        frame = Image.new("RGB", (1920, 1080), "white")
        with patch.object(Image.Image, "filter", spy):
            changed_percent(frame, frame.copy())
        self.assertTrue(filtered_sizes)
        self.assertTrue(all(w * h <= ANALYSIS_MAX_PIXELS for w, h in filtered_sizes))

    def test_large_target_keeps_area_semantics_and_ignores_caret_noise(self):
        before = Image.new("RGB", (1920, 1080), "white")
        dialog = before.copy()
        ImageDraw.Draw(dialog).rectangle((600, 300, 1199, 699), fill=(40, 40, 40))
        self.assertAlmostEqual(changed_percent(before, dialog),
                               100 * 600 * 400 / (1920 * 1080), delta=0.5)
        caret = before.copy()
        ImageDraw.Draw(caret).rectangle((900, 500, 901, 517), fill="black")
        caret.putpixel((10, 10), (0, 0, 0))
        self.assertLess(changed_percent(before, caret), 0.05)
        # Conservative mode stays at full resolution for small changes.
        self.assertGreater(changed_percent(before, caret, conservative=True), 0)

    def test_mask_clips_to_image_and_leaves_source_unchanged(self):
        image = Image.new("RGB", (10, 10), "white")
        mask = SimpleNamespace(left=-2, top=5, width=8, height=10)
        result = apply_masks(image, [mask])
        self.assertEqual(result.getpixel((0, 5)), (0, 0, 0))
        self.assertEqual(result.getpixel((5, 9)), (0, 0, 0))
        self.assertEqual(result.getpixel((6, 9)), (255, 255, 255))
        self.assertEqual(image.getpixel((0, 5)), (255, 255, 255))

    def test_changes_inside_mask_do_not_trigger_change(self):
        before = Image.new("RGB", (100, 100), "white")
        after = before.copy()
        ImageDraw.Draw(after).rectangle((10, 10, 29, 29), fill="red")
        mask = SimpleNamespace(left=10, top=10, width=20, height=20)
        self.assertEqual(changed_percent(apply_masks(before, [mask]), apply_masks(after, [mask])), 0)

    def test_click_marker_is_a_copy_and_outside_clicks_are_ignored(self):
        image = Image.new("RGB", (100, 100), "white")
        marked = mark_click(image, (50, 50))
        self.assertNotEqual(marked.tobytes(), image.tobytes())
        self.assertEqual(image.getpixel((50, 50)), (255, 255, 255))
        self.assertEqual(mark_click(image, None).tobytes(), image.tobytes())
        self.assertEqual(mark_click(image, (200, 50)).tobytes(), image.tobytes())


if __name__ == "__main__":
    unittest.main()
