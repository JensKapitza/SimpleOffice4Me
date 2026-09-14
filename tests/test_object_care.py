from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.library.object_care import (
    ObjectCareStore,
    _analysis_suggestions,
    _ocr_image,
)
from app.library.store import LibraryStore
from app.object_store import ObjectStore
from app.object_vision import _rapidocr_output, analyze_ocr


class UnifiedObjectCareTests(unittest.TestCase):
    def test_ocr_analysis_detects_compliance_and_identity_hints(self):
        result = _analysis_suggestions(
            "ACME Werkzeug CE DGUV V3 VDE RoHS\nModel: ZX-42\nSerial Number: ABC-123"
        )
        self.assertIn("CE", result["detected_terms"])
        self.assertIn("DGUV", result["detected_terms"])
        self.assertIn("VDE", result["detected_terms"])
        self.assertIn("RoHS", result["detected_terms"])
        self.assertEqual("erkannt", result["suggested_fields"]["ce_marking"])
        self.assertEqual("erkannt", result["suggested_fields"]["dguv_marking"])
        self.assertTrue(result["suggested_fields"]["model"].startswith("ZX-42"))
        self.assertTrue(result["suggested_fields"]["serial_number"].startswith("ABC-123"))

    def test_ml_ocr_output_keeps_confidence_and_boxes(self):
        class Result:
            txts = ("ACME", "Model ZX-42")
            scores = (0.96, 0.84)
            boxes = (
                ((10, 20), (80, 20), (80, 40), (10, 40)),
                ((10, 50), (140, 50), (140, 72), (10, 72)),
            )

        result = _rapidocr_output(Result())
        self.assertEqual("rapidocr", result["engine"])
        self.assertEqual("completed", result["status"])
        self.assertEqual("ACME\nModel ZX-42", result["text"])
        self.assertEqual(0.9, result["confidence"])
        self.assertEqual(2, len(result["blocks"]))
        self.assertEqual([[10.0, 20.0], [80.0, 20.0], [80.0, 40.0], [10.0, 40.0]], result["blocks"][0]["box"])

    def test_ocr_prefers_ml_and_does_not_call_tesseract_on_success(self):
        ml = {
            "engine": "rapidocr",
            "status": "completed",
            "text": "Bosch GWS 750",
            "characters": 13,
            "confidence": 0.93,
            "blocks": [],
        }
        with patch("app.object_vision._run_rapidocr", return_value=ml), patch(
            "app.object_vision._run_tesseract"
        ) as tesseract:
            result = analyze_ocr(Path("unused.jpg"))
        self.assertEqual("rapidocr", result["engine"])
        self.assertEqual("Bosch GWS 750", result["text"])
        tesseract.assert_not_called()

    def test_ocr_falls_back_to_tesseract_when_ml_is_unavailable(self):
        ml = {
            "engine": "rapidocr",
            "status": "unavailable",
            "text": "",
            "characters": 0,
            "confidence": None,
            "blocks": [],
            "error": "RapidOCR fehlt",
        }
        fallback = {
            "engine": "tesseract",
            "status": "completed",
            "text": "Seriennummer ABC-123",
            "characters": 21,
            "confidence": None,
            "blocks": [],
        }
        with patch("app.object_vision._run_rapidocr", return_value=ml), patch(
            "app.object_vision._run_tesseract", return_value=fallback
        ):
            result = analyze_ocr(Path("unused.jpg"))
        self.assertEqual("tesseract", result["engine"])
        self.assertEqual("rapidocr", result["fallback_from"])
        self.assertIn("RapidOCR fehlt", result["fallback_reason"])

    def test_legacy_ocr_wrapper_uses_shared_service(self):
        with patch(
            "app.library.object_care.analyze_ocr",
            return_value={"engine": "rapidocr", "status": "completed", "text": "CE VDE"},
        ):
            text, status, error = _ocr_image(Path("unused.jpg"))
        self.assertEqual("CE VDE", text)
        self.assertEqual("completed", status)
        self.assertEqual("", error)

    def test_condition_capture_keeps_history_and_latest_state(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectCareStore(Path(temp))
            first = store.record_condition(
                "object-1",
                {"state": "good", "rating": 4, "captured_on": "2026-09-01", "note": "kleine Gebrauchsspuren"},
                "tester",
            )
            second = store.record_condition(
                "object-1",
                {"state": "worn", "rating": 3, "captured_on": "2026-09-14", "note": "deutlich abgenutzt"},
                "tester",
            )
            meta = store.object_meta("object-1")
        self.assertEqual("good", first["state"])
        self.assertEqual("worn", second["state"])
        self.assertEqual(2, len(meta["condition_history"]))
        self.assertEqual("worn", meta["condition"]["state"])
        self.assertEqual(3, meta["condition"]["rating"])

    def test_condition_validation_rejects_unknown_state_and_rating(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectCareStore(Path(temp))
            with self.assertRaisesRegex(ValueError, "Objektzustand"):
                store.record_condition("object-1", {"state": "broken-ish", "rating": 3}, "tester")
            with self.assertRaisesRegex(ValueError, "zwischen 1 und 5"):
                store.record_condition("object-1", {"state": "good", "rating": 6}, "tester")

    def test_compliance_keeps_current_record_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ObjectCareStore(Path(temp))
            store.record_compliance(
                "object-1",
                {"scheme": "CE", "status": "present", "recorded_on": "2026-09-01", "reference": "Typenschild"},
                "tester",
            )
            store.record_compliance(
                "object-1",
                {"scheme": "CE", "status": "verified", "recorded_on": "2026-09-14", "reference": "Konformitätserklärung"},
                "tester",
            )
            store.record_compliance(
                "object-1",
                {"scheme": "DGUV V3", "status": "due", "recorded_on": "2026-09-14"},
                "tester",
            )
            meta = store.object_meta("object-1")
        self.assertEqual(3, len(meta["compliance_history"]))
        self.assertEqual("verified", meta["compliance"]["ce"]["status"])
        self.assertEqual("due", meta["compliance"]["dguv v3"]["status"])

    def test_library_location_is_created_as_canonical_object(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = LibraryStore(root)
            location = library.create_location("Regal A", "tester")
            self.assertTrue(location["object_id"])
            item = ObjectStore(root).object(location["object_id"])
        self.assertEqual("shelf", item["type"])
        self.assertEqual(location["code"], item["identifier"])
        self.assertEqual(location["location_id"], item["fields"]["library_location_id"])
        self.assertIn("Regal", item["tags"])

    def test_library_location_hierarchy_is_mirrored_to_shelf_object(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = LibraryStore(root)
            parent = library.create_location("Wohnzimmer", "tester")
            child = library.create_location("Regal 2", "tester", parent_id=parent["location_id"])
            item = ObjectStore(root).object(child["object_id"])
        self.assertEqual("Wohnzimmer", item["location"])
        self.assertEqual(parent["location_id"], item["fields"]["library_parent_id"])

    def test_sync_backfills_objects_for_legacy_library_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = LibraryStore(root)
            library.directory.mkdir(parents=True, exist_ok=True)
            library.state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "next_location": 2,
                        "locations": [
                            {
                                "location_id": "legacy-location",
                                "code": "LIB-L0001",
                                "name": "Altes Regal",
                                "parent_id": "",
                                "created_at": "2026-01-01T00:00:00Z",
                                "created_by": "tester",
                            }
                        ],
                        "assignments": {},
                        "events": [],
                    }
                ),
                encoding="utf-8",
            )
            rows = library.sync_location_objects("tester")
            location = library.location("legacy-location")
        self.assertEqual(1, len(rows))
        self.assertTrue(location["object_id"])

    def test_object_care_template_groups_media_condition_and_compliance(self):
        template = (
            Path(__file__).parents[1] / "templates" / "library" / "object_care.html"
        ).read_text(encoding="utf-8")
        for fragment in (
            "Ein Objekt, mehrere Fachansichten",
            "Bilder, OCR &amp; Analyse",
            "Zustand &amp; Alterungsverlauf",
            "Prüfungen &amp; Konformität",
            "DGUV V3",
            "CE",
            "Bestehende Regale als Objekte abgleichen",
            'name="_csrf_token"',
        ):
            self.assertIn(fragment, template)
        self.assertNotIn("=${{ value }}", template)

    def test_existing_object_and_inventory_pages_link_to_object_care(self):
        root = Path(__file__).parents[1]
        inventory = (root / "templates" / "inventory" / "detail.html").read_text(encoding="utf-8")
        object_detail = (root / "templates" / "documents" / "object_detail.html").read_text(encoding="utf-8")
        self.assertIn("library.object_care", inventory)
        self.assertIn("Foto speichern + OCR", inventory)
        self.assertIn("library.object_care", object_detail)
        self.assertIn("Bilder · Zustand · Prüfungen", object_detail)


if __name__ == "__main__":
    unittest.main()
