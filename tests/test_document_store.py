import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.document_store import CONTROL_DIR, POLICY_FILE, DocumentStore


class DocumentStoreTest(unittest.TestCase):
    def test_oversized_upload_is_removed_from_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DocumentStore(root)

            with self.assertRaisesRegex(ValueError, "size limit"):
                store.import_upload(io.BytesIO(b"123456"), "large.bin", "tester", max_bytes=5)

            staging = root / CONTROL_DIR / "staging"
            self.assertEqual([], list(staging.glob("*")))
            self.assertFalse((root / "inbox" / "large.bin").exists())

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

    def test_unchanged_file_reuses_cached_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "gross.bin"
            source.write_bytes(b"x" * 4096)
            store = DocumentStore(root)
            store.scan()

            with patch("app.document_store.sha256_file", side_effect=AssertionError("file was hashed again")):
                report = store.scan()

            self.assertEqual(1, report.files)
            self.assertEqual(0, report.new_files)

    def test_missing_metadata_is_rebuilt_from_cached_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "repair.bin"
            source.write_bytes(b"recoverable")
            store = DocumentStore(root)
            store.scan()
            original = store.get_document(source)
            metadata_path = root / CONTROL_DIR / "documents" / f"{original['document_id']}.json"
            metadata_path.unlink()

            with patch("app.document_store.sha256_file", side_effect=AssertionError("cached file was hashed again")):
                store.scan()

            repaired = store.get_document(source)
            self.assertEqual(original["document_id"], repaired["document_id"])
            self.assertTrue(metadata_path.is_file())

    def test_external_move_keeps_id_without_rehashing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "vorher.bin"
            source.write_bytes(b"content")
            store = DocumentStore(root)
            store.scan()
            original = store.get_document(source)
            destination = root / "nachher.bin"
            source.rename(destination)

            with patch("app.document_store.sha256_file", side_effect=AssertionError("moved file was hashed again")):
                store.scan()

            moved = store.get_document(destination)
            self.assertEqual(original["document_id"], moved["document_id"])
            self.assertEqual("vorher.bin", moved["location_history"][-1]["from"])
            self.assertEqual("nachher.bin", moved["location_history"][-1]["to"])
            self.assertNotEqual("duplicate", moved["system_state"])

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

    def test_document_page_does_not_load_the_complete_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for number in range(12):
                (root / f"datei-{number}.txt").write_text(str(number), encoding="utf-8")
            store = DocumentStore(root); store.scan()

            first = store.document_page(page=1, page_size=5)
            third = store.document_page(page=3, page_size=5)

            self.assertEqual(12, first["total"])
            self.assertEqual(5, len(first["documents"]))
            self.assertTrue(first["has_next"])
            self.assertEqual(2, len(third["documents"]))

    def test_search_is_paged_from_the_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for number in range(12):
                (root / f"suchbegriff-{number}.txt").write_text("Inhalt", encoding="utf-8")
            store = DocumentStore(root); store.scan()

            first = store.search_page("suchbegriff", page=1, page_size=5)
            third = store.search_page("suchbegriff", page=3, page_size=5)

            self.assertEqual(5, len(first["results"]))
            self.assertTrue(first["has_next"])
            self.assertEqual(2, len(third["results"]))
            self.assertFalse(third["has_next"])

    def test_boolean_retrieval_combines_tags_names_and_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "angebot.txt").write_text("Liefertermin am Freitag", encoding="utf-8")
            (root / "notiz.txt").write_text("anderer Inhalt", encoding="utf-8")
            store = DocumentStore(root); store.scan()
            store.set_tags("angebot.txt", ["rechnung"], author="tester")
            store.set_tags("notiz.txt", ["intern"], author="tester")

            results = store.search(
                "tag:rechnung UND (name:angebot ODER text:nichtvorhanden)"
            )

            self.assertEqual(["angebot.txt"], [item["path"] for item in results])

    def test_invalid_retrieval_query_does_not_fall_back_to_broad_search(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DocumentStore(temp)
            with self.assertRaisesRegex(ValueError, "Klammer"):
                store.search("tag:rechnung UND (name:angebot")

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

    def test_document_move_keeps_stable_id_and_records_location_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "inbox" / "rechnung.txt"
            source.parent.mkdir()
            source.write_text("Rechnung", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            original = store.get_document(source)

            moved = store.move_document(original["document_id"], "Rechnungen/2026", "tester")

            self.assertEqual(original["document_id"], moved["document_id"])
            self.assertEqual("Rechnungen/2026/rechnung.txt", moved["last_path"])
            self.assertTrue((root / moved["last_path"]).is_file())
            self.assertFalse(source.exists())
            self.assertEqual("inbox/rechnung.txt", moved["location_history"][-1]["from"])
            self.assertEqual(moved["document_id"], store.get_document(moved["last_path"])["document_id"])

    def test_accesses_and_persistent_share_links_are_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "angebot.txt"; source.write_text("Intern", encoding="utf-8")
            store = DocumentStore(root); store.scan(); document = store.get_document(source)

            store.record_access(document["document_id"], "admin", "found")
            store.record_access(document["document_id"], "jens", "seen")
            accessed = store.get_document(document["document_id"])
            self.assertIn("admin", accessed["found_by"])
            self.assertIn("jens", accessed["seen_by"])

            first = store.create_share(document["document_id"], "erstes-passwort", 7, "admin")
            second = store.create_share(document["document_id"], "zweites-passwort", 7, "admin")
            store.open_share(first["share_id"], "erstes-passwort", "198.51.100.24")
            self.assertEqual(2, len(store.document_shares(document["document_id"])))
            self.assertEqual("opened", store.share_status(first["share_id"])["access_log"][-1]["action"])
            self.assertEqual("198.51.100.24", store.share_status(first["share_id"])["access_log"][-1]["ip"])

            payload = store._read_json(store.shares_path, {"shares": []})
            next(item for item in payload["shares"] if item["share_id"] == first["share_id"])["expires_at"] = "2000-01-01T00:00:00+00:00"
            store.shares_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "abgelaufen"):
                store.open_share(first["share_id"], "erstes-passwort", "198.51.100.24")
            self.assertEqual("abgelaufen", store.share_status(first["share_id"])["status"])
            store.renew_share(document["document_id"], first["share_id"], "neues-passwort", 7, "admin")
            store.open_share(first["share_id"], "neues-passwort", "198.51.100.24")
            self.assertEqual("aktiv", store.share_status(first["share_id"])["status"])

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
            store.scan()
            document = store.get_document(pdf_path)
            self.assertIn("Bezugscode Kranich", document["extracted_text"])
            self.assertEqual(document["document_id"], store.search("Kranich")[0]["document_id"])
            document.pop("extracted_text")
            store._save_document(document)
            self.assertGreaterEqual(store.refresh_missing_text("tester"), 1)
            self.assertIn("Kranich", store.get_document(pdf_path)["extracted_text"])

    def test_pdf_text_falls_back_to_pypdf_without_pdftotext(self):
        try:
            from reportlab.pdfgen.canvas import Canvas
            import pypdf  # noqa: F401
        except ImportError:
            self.skipTest("PDF test dependencies are not installed")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf_path = root / "fallback.pdf"
            canvas = Canvas(str(pdf_path))
            canvas.drawString(72, 720, "Python PDF Fallback")
            canvas.save()

            original_which = shutil.which
            with patch(
                "app.document_store.shutil.which",
                side_effect=lambda command: None if command in {"pdftotext", "pdfimages"} else original_which(command),
            ):
                DocumentStore(root).scan()

            self.assertIn("Python PDF Fallback", DocumentStore(root).get_document(pdf_path)["extracted_text"])
