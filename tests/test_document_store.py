import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.document_store import CONTROL_DIR, POLICY_FILE, DocumentStore


class DocumentStoreTest(unittest.TestCase):
    def test_scan_creates_metadata_and_detects_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.txt").write_text("same content", encoding="utf-8")
            (root / "two.txt").write_text("same content", encoding="utf-8")

            report = DocumentStore(root).scan()

            self.assertEqual(2, report.files)
            self.assertEqual(2, report.new_files)
            self.assertEqual(1, report.duplicates)
            self.assertTrue((root / POLICY_FILE).exists())
            self.assertTrue((root / CONTROL_DIR / "events.ndjson").exists())

    def test_existing_policy_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            policy = {"version": 1, "folder_id": "known", "inherit": False, "grants": []}
            (root / POLICY_FILE).write_text(json.dumps(policy), encoding="utf-8")

            DocumentStore(root).initialize()

            self.assertEqual(policy, json.loads((root / POLICY_FILE).read_text(encoding="utf-8")))

    def test_symlink_is_logged_but_not_followed_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source.txt").write_text("content", encoding="utf-8")
            (root / "loop").symlink_to(root, target_is_directory=True)

            report = DocumentStore(root).scan()

            self.assertEqual(1, report.files)
            self.assertEqual(1, report.symlinks)

    def test_notes_links_states_and_versions_are_file_based(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.txt"
            source.write_text("version one", encoding="utf-8")
            related = root / "related.txt"
            related.write_text("related", encoding="utf-8")
            replacement = root / "replacement.txt"
            replacement.write_text("version two", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()

            first = store.get_document("source.txt")
            second = store.get_document("related.txt")
            note = store.add_note(first["document_id"], "Bitte prüfen", "tester")
            store.set_state(first["document_id"], "in_pruefung", "tester")
            store.set_attribute(first["document_id"], "projekt", "Musterbau", "tester")
            store.add_link(first["document_id"], second["document_id"], "bezieht_sich_auf", author="tester")
            version = store.import_version(replacement, first["document_id"], "tester")
            graph = store.graph(first["document_id"])

            self.assertEqual("Bitte prüfen", note["text"])
            self.assertEqual("in_pruefung", store.get_document(first["document_id"])["state"])
            self.assertEqual(2, version["version_number"])
            self.assertIn(version["document_id"], {node["id"] for node in graph["nodes"]})
            self.assertEqual(first["document_id"], store.search("Musterbau")[0]["document_id"])

    def test_wiki_versions_and_logbook_are_readable_from_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_file = root / "angebot.txt"
            first_file.write_text("erste Fassung", encoding="utf-8")
            replacement = root / "angebot-neu.txt"
            replacement.write_text("zweite Fassung", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            first = store.get_document("angebot.txt")
            store.add_note(first["document_id"], "Kunde hat bestätigt", "tester")
            store.import_version(replacement, first["document_id"], "tester")

            self.assertEqual(2, len(store.versions(first["document_id"])))
            self.assertEqual("Kunde hat bestätigt", store.note_wiki()[0]["text"])
            self.assertTrue(any(event.get("action") == "document_note_added" for event in store.logbook(first["document_id"])))

    def test_logbook_is_paginated_and_filterable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DocumentStore(Path(temp))
            store.initialize()
            for number in range(75):
                store._event("test_action", {"actor": "jens" if number % 2 else "other", "number": number})

            first = store.logbook_page(page=1, page_size=50)
            filtered = store.logbook_page(page=1, page_size=50, actor="jens", action="test_action")

            self.assertEqual(50, len(first["events"]))
            self.assertTrue(first["has_next"])
            self.assertEqual(37, len(filtered["events"]))
            self.assertTrue(all(item["actor"] == "jens" for item in filtered["events"]))

    def test_tags_support_prefix_and_wildcard_matching(self):
        self.assertTrue(DocumentStore.tag_matches("dank", "danke"))
        self.assertTrue(DocumentStore.tag_matches("dan*", "danke"))
        self.assertFalse(DocumentStore.tag_matches("bit", "danke"))

    def test_changed_original_is_reported_as_integrity_problem(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.txt"
            source.write_text("original", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            source.write_text("changed outside application", encoding="utf-8")

            store.scan()

            self.assertEqual("integrity_changed", store.get_document("source.txt")["system_state"])

    def test_image_scan_extracts_exif_runs_ocr_and_generates_tags(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "Werkstatt Rechnung 2026.jpg"
            exif = Image.Exif()
            exif[271] = "SimpleCamera"
            exif[272] = "Test 1"
            Image.new("RGB", (80, 40), "white").save(image_path, exif=exif)
            store = DocumentStore(root)
            store.scan()
            image = store.get_document(image_path)

            self.assertEqual("JPEG", image["image_analysis"]["format"])
            self.assertEqual(80, image["image_analysis"]["width"])
            self.assertEqual("SimpleCamera", image["image_analysis"]["exif"]["Make"])
            self.assertIn("bild", image["tags"])
            self.assertIn("format-jpg", image["tags"])
            self.assertIn("kamera-simplecamera-test-1", image["tags"])
            self.assertIn(image["image_analysis"]["ocr_status"], {"completed", "unavailable"})

    def test_pdf_text_is_extracted_searchable_and_backfilled_when_missing(self):
        try:
            from reportlab.pdfgen.canvas import Canvas
        except ImportError:
            self.skipTest("reportlab is not installed")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_path = root / "rechnung.pdf"
            canvas = Canvas(str(pdf_path))
            canvas.drawString(72, 720, "Durchsuchbarer Bezugscode Kranich")
            canvas.save()
            store = DocumentStore(root)
            with mock.patch("app.document_store.shutil.which", return_value=None):
                store.scan()
            document = store.get_document(pdf_path)
            self.assertIn("Bezugscode Kranich", document["extracted_text"])
            self.assertEqual(document["document_id"], store.search("Kranich")[0]["document_id"])
            document.pop("extracted_text")
            store._save_document(document)
            self.assertGreaterEqual(store.refresh_missing_text("tester"), 1)
            self.assertIn("Kranich", store.get_document(pdf_path)["extracted_text"])
