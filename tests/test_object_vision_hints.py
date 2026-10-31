from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.object_vision import analyze_ocr, ocr_availability


class OcrHintTests(unittest.TestCase):
    def test_missing_rapidocr_names_pip_extra(self):
        with patch("app.object_vision._RAPID_OCR_ENGINE", None), patch(
            "app.object_vision.find_spec", return_value=None,
        ):
            result = analyze_ocr(Path("unused.jpg"), fallback_tesseract=False)
        self.assertEqual("rapidocr", result["engine"])
        self.assertEqual("unavailable", result["status"])
        self.assertIn("RapidOCR ist nicht installiert", result["error"])
        self.assertIn("install .[ocr]", result["error"])

    def test_missing_tesseract_names_windows_install(self):
        ml = {
            "engine": "rapidocr",
            "status": "unavailable",
            "text": "",
            "characters": 0,
            "confidence": None,
            "blocks": [],
            "error": "RapidOCR fehlt",
        }
        with (
            patch("app.object_vision._run_rapidocr", return_value=ml),
            patch("app.object_vision.shutil.which", return_value=None),
        ):
            result = analyze_ocr(Path("unused.jpg"))
        self.assertEqual("tesseract", result["fallback_engine"])
        self.assertEqual("unavailable", result["fallback_status"])
        self.assertIn("Tesseract", result["error"])
        self.assertIn("PATH", result["error"])

    def test_availability_reports_both_backends(self):
        spec = SimpleNamespace(submodule_search_locations=["/x"])
        with (
            patch("app.object_vision.find_spec", return_value=spec),
            patch("app.object_vision.shutil.which", return_value="/usr/bin/tesseract"),
        ):
            self.assertEqual(
                {"rapidocr": True, "tesseract": True}, ocr_availability()
            )
        with (
            patch("app.object_vision.find_spec", return_value=None),
            patch("app.object_vision.shutil.which", return_value=None),
        ):
            self.assertEqual(
                {"rapidocr": False, "tesseract": False}, ocr_availability()
            )

    def test_availability_tolerates_broken_spec(self):
        with (
            patch("app.object_vision.find_spec", side_effect=ValueError("bad")),
            patch("app.object_vision.shutil.which", return_value=None),
        ):
            self.assertEqual(
                {"rapidocr": False, "tesseract": False}, ocr_availability()
            )


if __name__ == "__main__":
    unittest.main()
