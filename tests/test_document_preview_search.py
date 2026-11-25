"""Document text preview: search, copy actions and extraction status markers."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app import app
from app import db as database
from app.document_store import DocumentStore


class DocumentTextPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "alice-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "alice-password-123"})
        store = self.store = DocumentStore(base / "documents")
        store.initialize()
        self.text_doc = store.import_upload(
            io.BytesIO("Alpha Bravo Alpha Charlie\nZeile zwei\n".encode("utf-8")), "notizen.txt", "alice",
        )
        self.pdf_doc = store.import_upload(io.BytesIO(b"%PDF-1.4\nbinary"), "bericht.pdf", "alice")
        pdf = store.get_document(self.pdf_doc["document_id"])
        pdf["extracted_text"] = "Erkannter Berichtstext mit Suchwort Kolibri"
        pdf["text_extraction"] = {"status": "completed", "updated_by": "alice"}
        store._save_document(pdf)

    def tearDown(self):
        app.config.update(self.saved)

    def test_text_preview_offers_search_and_copy_actions(self):
        html = self.client.get(f"/documents/{self.text_doc['document_id']}").get_data(as_text=True)
        for marker in (
            "data-text-preview", "data-text-preview-search", "data-text-preview-prev",
            "data-text-preview-next", "data-text-preview-count", "data-text-preview-copy",
            "data-text-preview-copy-selection", "js/document_preview.js",
        ):
            self.assertIn(marker, html)
        self.assertIn("Alpha Bravo Alpha", html)
        self.assertIn('aria-keyshortcuts="Enter Shift+Enter"', html)

    def test_extraction_status_is_shown_for_recognized_text(self):
        path = f"/documents/{self.pdf_doc['document_id']}"
        pdf = self.store.get_document(self.pdf_doc["document_id"])
        pdf["text_extraction"].update(source_sha256=pdf["sha256"], extracted_at="2026-11-24T18:47:02+01:00",
                                      updated_by="synthetic-index-actor")
        self.store._save_document(pdf)
        for language, title in (("en", "Recognized text"), ("de", "Erkannter Text")):
            with self.subTest(language=language):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = language
                page = BeautifulSoup(self.client.get(path).data, "html.parser")
                line = page.select_one("[data-text-preview-source]")
                self.assertIn(title, line.get_text())
                self.assertIn("Kolibri", page.get_text())
                self.assertNotIn("synthetic-index-actor", line.get_text())
                moment = line.select_one("time")
                self.assertEqual("2026-11-24T17:47:02+00:00", moment["datetime"])
                self.assertEqual("2026-11-24 17:47:02 UTC", moment.get_text(strip=True))

    def test_extraction_time_is_absent_without_a_verified_aware_timestamp(self):
        pdf = self.store.get_document(self.pdf_doc["document_id"])
        path = f"/documents/{pdf['document_id']}"
        for extracted, digest in (("not a date", pdf["sha256"]), ("2026-11-24T18:47:02", pdf["sha256"]),
                                  ("2026-11-24T18:47:02+00:00", "d" * 64)):
            with self.subTest(extracted=extracted, digest=digest):
                pdf["text_extraction"].update(source_sha256=digest, extracted_at=extracted,
                                              updated_by="synthetic-index-actor")
                self.store._save_document(pdf)
                page = BeautifulSoup(self.client.get(path).data, "html.parser")
                line = page.select_one("[data-text-preview-source]")
                if line:
                    self.assertIsNone(line.select_one("time"))
                    self.assertNotIn("synthetic-index-actor", line.get_text())

    def test_plain_text_has_no_extraction_line(self):
        html = self.client.get(f"/documents/{self.text_doc['document_id']}").get_data(as_text=True)
        self.assertNotIn("data-text-preview-source", html)

    def test_preview_script_stays_safe(self):
        script = (Path(__file__).resolve().parent.parent / "static" / "js" / "document_preview.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertIn("clipboard", script)
        self.assertIn("data-preview-match", script)


    def test_photo_uses_its_own_analysis_time_not_the_index_refresh_time(self):
        from PIL import Image
        from unittest.mock import patch

        jpeg = io.BytesIO()
        Image.new("RGB", (10, 10), "white").save(jpeg, format="JPEG")
        jpeg.seek(0)
        with patch.object(DocumentStore, "_image_ocr", return_value="recognized photo"):
            photo = self.store.import_upload(jpeg, "camera.jpg", "alice")
        photo = self.store.get_document(photo["document_id"])
        photo["image_analysis"]["analyzed_at"] = "2026-11-24T08:01:02+00:00"
        photo["text_extraction"]["extracted_at"] = "2026-11-24T10:30:02+00:00"
        self.store._save_document(photo)
        path = f"/documents/{photo['document_id']}"
        line = BeautifulSoup(self.client.get(path).data, "html.parser").select_one("[data-text-preview-source]")
        self.assertEqual("2026-11-24T08:01:02+00:00", line.select_one("time")["datetime"])
        # A failed retry can keep last-good text without recording its earlier
        # timestamp. The preview must not display a fresh success time then.
        photo["image_analysis"]["ocr_retry_status"] = "unavailable"
        self.store._save_document(photo)
        line = BeautifulSoup(self.client.get(path).data, "html.parser").select_one("[data-text-preview-source]")
        self.assertIsNotNone(line)
        self.assertIsNone(line.select_one("time"))


class TextPreviewFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend tests")
    def test_preview_search_preserves_text_and_navigates_latest_query(self):
        script = Path(__file__).with_name("document_preview_navigation.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
