from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.object_vision import analyze_ocr


class ObjectVisionFallbackTests(unittest.TestCase):
    def test_tesseract_runs_when_rapidocr_initialization_fails(self):
        fallback = {
            "engine": "tesseract",
            "status": "completed",
            "text": "Seriennummer ABC-123",
            "characters": 21,
            "confidence": None,
            "blocks": [],
        }
        with patch(
            "app.object_vision._rapidocr_engine",
            side_effect=OSError("read-only model cache"),
        ), patch("app.object_vision._run_tesseract", return_value=fallback) as tesseract:
            result = analyze_ocr(Path("unused.jpg"))

        tesseract.assert_called_once()
        self.assertEqual("tesseract", result["engine"])
        self.assertEqual("rapidocr", result["fallback_from"])
        self.assertIn("read-only model cache", result["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
