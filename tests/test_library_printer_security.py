"""Security limits for user-controlled label payloads."""
from __future__ import annotations

import io
import unittest

from PIL import Image

from app.library.printer import PrinterError, render_barcode, render_image


class LabelRenderingSecurityTests(unittest.TestCase):
    settings = {"label": "62", "font_name": "DejaVuSans", "font_size": 32}

    def test_short_barcode_still_renders(self):
        result = render_barcode("BOOK-123", self.settings)
        self.assertEqual(696, result.width)

    def test_barcode_above_explicit_input_limit_is_rejected(self):
        with self.assertRaises(PrinterError):
            render_barcode("A" * 121, self.settings)

    def test_barcode_must_fit_selected_printable_width(self):
        with self.assertRaises(PrinterError):
            render_barcode("A" * 90, self.settings)

    def test_code128_render_keeps_a_blank_scan_quiet_zone(self):
        image = render_barcode(
            "4006381333931", self.settings, show_text=False, scan_optimized=True
        )
        dark_columns = [
            x
            for y in range(12, image.height - 12)
            for x in range(image.width)
            if image.getpixel((x, y)) == (0, 0, 0)
        ]

        self.assertTrue(dark_columns)
        self.assertGreaterEqual(min(dark_columns), 30)
        self.assertLessEqual(max(dark_columns), image.width - 31)

    def test_scan_optimized_barcode_rejects_a_too_small_label(self):
        settings = {**self.settings, "label": "29x90"}
        with self.assertRaisesRegex(PrinterError, "größeres Etikett"):
            render_barcode("SO-BOOK-2", settings, scan_optimized=True)

        # Existing book/PDF labels retain their previous rendering contract.
        image = render_barcode("SO-BOOK-2", settings)
        self.assertEqual(306, image.width)

    def test_normal_picture_still_renders(self):
        image = Image.new("RGB", (48, 32), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        result = render_image(buffer.getvalue(), self.settings)
        self.assertEqual(696, result.width)

    def test_valid_image_above_24_megapixels_is_rejected(self):
        buffer = io.BytesIO()
        with Image.new("RGB", (6000, 5000), "white") as image:
            image.save(buffer, format="PNG")
        with self.assertRaises(PrinterError):
            render_image(buffer.getvalue(), self.settings)


if __name__ == "__main__":
    unittest.main()
