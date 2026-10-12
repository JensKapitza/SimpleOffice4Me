"""Security limits for user-controlled label payloads."""
from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

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

    def test_normal_picture_still_renders(self):
        image = Image.new("RGB", (48, 32), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        result = render_image(buffer.getvalue(), self.settings)
        self.assertEqual(696, result.width)

    def test_image_bomb_is_rejected_before_pixel_decode(self):
        fake_image = MagicMock()
        fake_image.width = 6000
        fake_image.height = 5000
        fake_image.__enter__.return_value = fake_image
        with patch("app.library.printer.Image.open", return_value=fake_image):
            with self.assertRaises(PrinterError):
                render_image(b"not-decoded-yet", self.settings)
        fake_image.load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
