from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.ocr_preprocessing import needs_ocr_supplement, prepare_ocr_image


class OcrPreprocessingTests(unittest.TestCase):
    def test_sparse_or_low_confidence_text_requests_a_supplement(self):
        self.assertTrue(needs_ocr_supplement("short result", 0.99))
        self.assertTrue(needs_ocr_supplement("recognized words " * 8, 0.71))

    def test_missing_or_non_finite_confidence_requests_a_supplement(self):
        text = "recognized words " * 8
        for confidence in (None, "invalid", float("nan"), float("inf")):
            with self.subTest(confidence=confidence):
                self.assertTrue(needs_ocr_supplement(text, confidence))

    def test_long_high_confidence_text_does_not_request_a_supplement(self):
        self.assertFalse(needs_ocr_supplement("recognized words " * 8, 0.72))

    def test_prepare_ocr_image_writes_grayscale_at_original_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "prepared.png"
            Image.new("RGB", (64, 32), (240, 24, 48)).save(source)

            self.assertEqual((64, 32), prepare_ocr_image(source, target))
            with Image.open(target) as prepared:
                self.assertEqual("L", prepared.mode)
                self.assertEqual((64, 32), prepared.size)

    def test_prepare_ocr_image_rejects_rasters_over_the_pixel_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "prepared.png"
            Image.new("RGB", (11, 10), "white").save(source)

            with patch("app.ocr_preprocessing.MAX_OCR_PIXELS", 100):
                with self.assertRaisesRegex(RuntimeError, "Pixellimit"):
                    prepare_ocr_image(source, target)

            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
