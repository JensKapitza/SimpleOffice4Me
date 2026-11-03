"""OCR title/author/subtitle hierarchy for book covers (issue #658 follow-up).

RapidOCR returns blocks in text order, so the old ``lines[0]`` prefill
mistook the author line for the book title. ``split_cover_hierarchy``
uses relative type size and position instead. All fixtures below are
invented license-free lines with a cover-like geometry profile; the real
user-supplied cover is measured locally only and never committed.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.capture_analysis import split_cover_hierarchy


def _block(text, center_y, height, confidence=0.98, center_x=150.0,
           width=100.0):
    y0 = center_y - height / 2.0
    y1 = center_y + height / 2.0
    x0 = center_x - width / 2.0
    x1 = center_x + width / 2.0
    return {
        "text": text,
        "confidence": confidence,
        "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
    }


def _cover_blocks():
    """Invented cover: small author, two-line tall title, small subtitle."""
    return [
        _block("Jonas Berger", 24.0, 28.0, 0.9846),
        _block("WILDER", 76.0, 100.0, 0.998),
        _block("GARTEN", 128.0, 77.0, 0.9999),
        _block("für", 156.0, 30.0, 0.9755),
        _block("kleine Entdecker", 178.0, 65.0, 0.9679),
        _block("Edition", 217.0, 29.0, 0.9999),
        _block("Natur", 230.0, 33.0, 0.9988),
        _block("500", 277.0, 20.0, 0.9997),
        _block("9783161484100", 300.0, 22.0, 0.99),
        _block("xq zettel", 311.0, 18.0, 0.50),
    ]


class SplitCoverHierarchyTests(unittest.TestCase):
    def test_splits_author_title_subtitle(self) -> None:
        result = split_cover_hierarchy(_cover_blocks())
        self.assertTrue(result["title_confident"])
        self.assertEqual("WILDER GARTEN", result["title"])
        self.assertEqual(["Jonas Berger"], result["authors"])
        self.assertEqual("für kleine Entdecker", result["subtitle"])
        self.assertTrue(result["title_evidence"])
        self.assertNotIn("Jonas Berger", result["title"])
        self.assertNotIn("9783161484100", result["title"])

    def test_no_geometry_stays_unassigned(self) -> None:
        blocks = [
            {"text": "Jonas Berger", "confidence": 0.99},
            {"text": "WILDER GARTEN", "confidence": 0.99},
        ]
        result = split_cover_hierarchy(blocks)
        self.assertFalse(result["title_confident"])
        self.assertEqual("", result["title"])
        self.assertEqual([], result["authors"])
        self.assertEqual("", result["subtitle"])

    def test_low_confidence_title_stays_unassigned(self) -> None:
        blocks = _cover_blocks()
        blocks[1] = dict(blocks[1], confidence=0.70)
        result = split_cover_hierarchy(blocks)
        self.assertFalse(result["title_confident"])
        self.assertEqual("", result["title"])

    def test_uniform_sizes_stay_unassigned(self) -> None:
        blocks = [
            _block("Erste Zeile", 50.0, 24.0),
            _block("Zweite Zeile", 80.0, 24.0),
            _block("Dritte Zeile", 110.0, 24.0),
        ]
        result = split_cover_hierarchy(blocks)
        self.assertFalse(result["title_confident"])
        self.assertEqual("", result["title"])

    def test_non_list_input_stays_unassigned(self) -> None:
        for bad in (None, {}, "WILDER GARTEN"):
            result = split_cover_hierarchy(bad)
            self.assertFalse(result["title_confident"])
            self.assertEqual("", result["title"])

    def test_single_dominant_line_with_author(self) -> None:
        blocks = [
            _block("Jonas Berger", 24.0, 24.0),
            _block("WILDERGARTEN", 120.0, 90.0),
            _block("Ein Roman", 200.0, 26.0),
        ]
        result = split_cover_hierarchy(blocks)
        self.assertTrue(result["title_confident"])
        self.assertEqual("WILDERGARTEN", result["title"])
        self.assertEqual(["Jonas Berger"], result["authors"])
        self.assertEqual("Ein Roman", result["subtitle"])


class AnalyzePhotoHierarchyTests(unittest.TestCase):
    def _analyze(self, blocks):
        from app.inventory_capture import analyze_photo

        ocr_text = "\n".join(
            str(block.get("text", "")) for block in blocks
            if isinstance(block, dict)
        )
        ocr_payload = {
            "text": ocr_text, "engine": "rapidocr", "status": "completed",
            "characters": len(ocr_text), "confidence": 0.97,
            "blocks": blocks, "error": "",
        }
        ml_payload = {
            "available": False, "label": "", "item_type": "",
            "isbn": "", "note": "", "subtitle": "", "authors": [],
            "tags": [], "provider": "", "provider_label": "",
            "model": "", "error": "ML: Ollama nicht erreichbar",
        }
        with mock.patch(
            "app.library.ocr_input.optimize_upload",
            return_value=(b"png", "capture.png"),
        ), mock.patch(
            "app.inventory_capture.capture_ocr", return_value=ocr_payload,
        ), mock.patch(
            "app.inventory_capture.capture_ml_label",
            return_value=dict(ml_payload),
        ), mock.patch(
            "app.inventory_capture.capture_duplicates", return_value=[],
        ), mock.patch(
            "app.inventory_capture.capture_metadata", return_value={},
        ):
            return analyze_photo(b"fake-image", "photo-1")

    def test_author_is_not_prefilled_as_title(self) -> None:
        result = self._analyze(_cover_blocks())
        self.assertTrue(result["ok"])
        self.assertEqual("book", result["kind"])
        prefill = result["prefill"]
        self.assertEqual("WILDER GARTEN", prefill["title"])
        self.assertEqual(["Jonas Berger"], prefill["authors"])
        self.assertIn("kleine Entdecker", prefill["description"])
        self.assertEqual("ocr-hierarchie", prefill["metadata_source"])
        self.assertTrue(prefill["title_evidence"])
        self.assertNotIn("title", result["missing_fields"])

    def test_uncertain_title_stays_empty_and_missing(self) -> None:
        blocks = [
            _block("Jonas Berger", 24.0, 24.0),
            _block("Erste Zeile", 80.0, 24.0),
            _block("Zweite Zeile", 110.0, 24.0),
            _block("9783161484100", 200.0, 22.0),
        ]
        result = self._analyze(blocks)
        prefill = result["prefill"]
        self.assertEqual("", prefill["title"])
        self.assertIn("title", result["missing_fields"])


class UpscaleSmallCaptureTests(unittest.TestCase):
    def _png(self, width: int, height: int) -> bytes:
        import io as _io

        from PIL import Image

        buffer = _io.BytesIO()
        Image.new("RGB", (width, height), (200, 30, 30)).save(
            buffer, format="PNG")
        return buffer.getvalue()

    def test_small_image_is_doubled(self) -> None:
        from app.inventory_capture_vision import upscale_small_capture

        from PIL import Image
        import io as _io

        with Image.open(_io.BytesIO(
                upscale_small_capture(self._png(100, 150)))) as frame:
            self.assertEqual((200, 300), frame.size)

    def test_large_image_is_untouched(self) -> None:
        from app.inventory_capture_vision import upscale_small_capture

        large = self._png(1200, 800)
        self.assertEqual(large, upscale_small_capture(large))

    def test_garbage_bytes_fall_back_to_input(self) -> None:
        from app.inventory_capture_vision import upscale_small_capture

        self.assertEqual(b"kein-bild", upscale_small_capture(b"kein-bild"))

    def test_capture_ocr_feeds_upscaled_pixels(self) -> None:
        from app.inventory_capture_vision import capture_ocr

        from PIL import Image

        seen: dict[str, tuple[int, int]] = {}

        def fake_analyze(path):
            with Image.open(path) as frame:
                seen["size"] = (frame.width, frame.height)
            return {"text": "", "engine": "rapidocr", "status": "completed",
                    "characters": 0, "confidence": None, "blocks": [],
                    "error": ""}

        with mock.patch("app.object_vision.analyze_ocr",
                         side_effect=fake_analyze):
            result = capture_ocr(self._png(100, 150))
        self.assertEqual((200, 300), seen["size"])
        self.assertEqual([], result["blocks"])


if __name__ == "__main__":
    unittest.main()
