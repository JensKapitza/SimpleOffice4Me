"""Bulk document actions: multi-select tags and move with per-item results."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.document_store import DocumentStore


class BulkDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "alice-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "alice-password-123"})
        store = DocumentStore(base / "documents")
        store.initialize()
        self.first = store.import_upload(io.BytesIO(b"bulk one"), "one.txt", "alice")
        self.second = store.import_upload(io.BytesIO(b"bulk two"), "two.txt", "alice")

    def tearDown(self):
        app.config.update(self.saved)

    def test_bulk_tags_updates_all_and_reports_each(self):
        response = self.client.post(
            "/documents/bulk-tags",
            json={"ids": [self.first["document_id"], self.second["document_id"]], "tags": "bulk, neu"},
        )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertEqual(2, len(body["updated"]))
        self.assertEqual([], body["failed"])
        store = DocumentStore(Path(app.config["DOCUMENT_ROOT"]))
        tags = store.get_document(self.first["document_id"]).get("tags", [])
        self.assertIn("bulk", tags)

    def test_bulk_move_keeps_ids_and_reports_partial_failure(self):
        response = self.client.post(
            "/documents/bulk-move",
            json={"ids": [self.first["document_id"], "missing-id"], "folder": "archiv/2026"},
        )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertEqual([self.first["document_id"]], body["updated"])
        self.assertEqual(1, len(body["failed"]))
        store = DocumentStore(Path(app.config["DOCUMENT_ROOT"]))
        moved = store.get_document(self.first["document_id"])
        self.assertEqual("archiv/2026/one.txt", moved["last_path"])

    def test_bulk_endpoints_validate_input_and_login(self):
        too_many = self.client.post("/documents/bulk-tags", json={"ids": ["x"] * 201, "tags": "t"})
        self.assertEqual(400, too_many.status_code)
        empty_tags = self.client.post(
            "/documents/bulk-tags", json={"ids": [self.first["document_id"]], "tags": "  "},
        )
        self.assertEqual(400, empty_tags.status_code)
        anonymous = app.test_client()
        response = anonymous.post(
            "/documents/bulk-tags", json={"ids": ["x"], "tags": "t"}, follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

    def test_tree_offers_selection_and_bulk_bar(self):
        html = self.client.get("/documents/").get_data(as_text=True)
        for marker in (
            "data-bulk-select", "data-bulk-bar", "data-bulk-tags", "data-bulk-folder",
            "data-bulk-progress", "js/documents_bulk.js",
        ):
            self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
