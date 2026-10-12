"""Tests for image and PDF input validation at the OCR boundary."""
import io
import unittest
from unittest.mock import patch

from PIL import Image
from app.library.ocr_input import optimize_upload


class OCRInputTests(unittest.TestCase):
    def test_png_is_detected_and_optimized(self):
        image = Image.new("RGB", (80, 80), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        result, kind = optimize_upload(buffer.getvalue(), straighten=False)
        self.assertEqual(kind, "image")
        self.assertTrue(result.startswith(bytes([0x89]) + b"PNG"))

    def test_reject_unknown_bytes(self):
        with self.assertRaises(ValueError):
            optimize_upload(b"not a supported file")

    def test_reject_large_upload(self):
        with self.assertRaises(ValueError):
            optimize_upload(b"x" * (12 * 1024 * 1024 + 1))

    def test_reject_invalid_pdf_page_without_leaking_parser_errors(self):
        with self.assertRaises(ValueError):
            optimize_upload(b"%PDF-1.4\\ninvalid", page=1)


if __name__ == "__main__":
    unittest.main()
