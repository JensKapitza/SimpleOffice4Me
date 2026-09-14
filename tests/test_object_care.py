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

    def test_ocr_degrades_cleanly_when_tesseract_is_missing(self):
        with patch("app.library.object_care.shutil.which", return_value=None):
            text, status, error = _ocr_image(Path("unused.jpg"))
        self.assertEqual("", text)
        self.assertEqual("unavailable", status)
        self.assertIn("Tesseract", error)

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
