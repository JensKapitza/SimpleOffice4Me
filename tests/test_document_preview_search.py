"""Document text preview: search, copy actions and extraction status markers."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

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
        store = DocumentStore(base / "documents")
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

    def test_extraction_status_is_shown_for_recognized_text(self):
        html = self.client.get(f"/documents/{self.pdf_doc['document_id']}").get_data(as_text=True)
        self.assertIn("data-text-preview-source", html)
        self.assertIn("Kolibri", html)
        self.assertIn("completed", html)

    def test_plain_text_has_no_extraction_line(self):
        html = self.client.get(f"/documents/{self.text_doc['document_id']}").get_data(as_text=True)
        self.assertNotIn("data-text-preview-source", html)

    def test_preview_script_stays_safe(self):
        script = (Path(__file__).resolve().parent.parent / "static" / "js" / "document_preview.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertIn("clipboard", script)
        self.assertIn("data-preview-match", script)


if __name__ == "__main__":
    unittest.main()
