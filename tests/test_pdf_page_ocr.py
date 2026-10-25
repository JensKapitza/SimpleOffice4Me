from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.document_store import DocumentStore


class PdfPageOcrTests(unittest.TestCase):
    def setUp(self):
        self.store = DocumentStore.__new__(DocumentStore)
        self.source = Path("source.png")
        self.target = Path("prepared.png")

    def test_confident_rapidocr_result_skips_tesseract(self):
        text = "recognized invoice data " * 8
        result = {"status": "completed", "text": text, "confidence": 0.93}
        with patch("app.ocr_preprocessing.prepare_ocr_image") as prepare, patch(
            "app.object_vision.analyze_ocr", return_value=result,
        ), patch.object(DocumentStore, "_image_ocr") as tesseract:
            extracted, engines = self.store._pdf_page_ocr(self.source, self.target, "/mock/tesseract")

        self.assertEqual(text.strip(), extracted)
        self.assertEqual(["rapidocr"], engines)
        prepare.assert_called_once_with(self.source, self.target)
        tesseract.assert_not_called()

    def test_low_confidence_rapidocr_is_supplemented_with_non_duplicate_tesseract_lines(self):
        result = {
            "status": "completed",
            "text": "duplicate  line\nrecognized invoice data",
            "confidence": 0.51,
        }
        with patch("app.ocr_preprocessing.prepare_ocr_image"), patch(
            "app.object_vision.analyze_ocr", return_value=result,
        ), patch.object(
            DocumentStore, "_image_ocr", return_value="duplicate line\nadditional reference",
        ) as tesseract:
            extracted, engines = self.store._pdf_page_ocr(self.source, self.target, "/mock/tesseract")

        self.assertEqual(
            "duplicate  line\nrecognized invoice data\nadditional reference",
            extracted,
        )
        self.assertEqual(["rapidocr", "tesseract"], engines)
        tesseract.assert_called_once_with(self.target, timeout=30)

    def test_unavailable_rapidocr_falls_back_to_tesseract(self):
        result = {"status": "unavailable", "text": "", "confidence": None}
        with patch("app.ocr_preprocessing.prepare_ocr_image"), patch(
            "app.object_vision.analyze_ocr", return_value=result,
        ), patch.object(DocumentStore, "_image_ocr", return_value="invoice number 123") as tesseract:
            extracted, engines = self.store._pdf_page_ocr(self.source, self.target, "/mock/tesseract")

        self.assertEqual("invoice number 123", extracted)
        self.assertEqual(["tesseract"], engines)
        tesseract.assert_called_once_with(self.target, timeout=30)

    def test_unavailable_both_engines_returns_a_clear_failure(self):
        result = {"status": "unavailable", "text": "", "confidence": None}
        with patch("app.ocr_preprocessing.prepare_ocr_image"), patch(
            "app.object_vision.analyze_ocr", return_value=result,
        ), patch.object(DocumentStore, "_image_ocr") as tesseract:
            with self.assertRaisesRegex(RuntimeError, "RapidOCR.*Tesseract"):
                self.store._pdf_page_ocr(self.source, self.target, None)

        tesseract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
