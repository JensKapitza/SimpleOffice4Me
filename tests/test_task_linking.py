"""Unified linking: attach documents to tasks, backlinks, broken references."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.todo_store import TodoStore


class TaskLinkingTests(unittest.TestCase):
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
        self.document = store.import_upload(io.BytesIO(b"link me"), "vertrag.txt", "alice")
        self.task = TodoStore(base / "documents").add("Vertrag prüfen", "alice", {})

    def tearDown(self):
        app.config.update(self.saved)

    def test_attach_document_links_task_and_shows_backlink(self):
        response = self.client.post(
            f"/tasks/{self.task['id']}/documents",
            data={"document_id": self.document["document_id"]}, follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        board = self.client.get("/tasks/").get_data(as_text=True)
        self.assertIn("vertrag.txt", board)
        detail = self.client.get(f"/documents/{self.document['document_id']}").get_data(as_text=True)
        self.assertIn("Vertrag prüfen", detail)

    def test_attach_is_idempotent_and_validates(self):
        url = f"/tasks/{self.task['id']}/documents"
        self.client.post(url, data={"document_id": self.document["document_id"]})
        self.client.post(url, data={"document_id": self.document["document_id"]})
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        task = next(item for item in store.items("alice") if item["id"] == self.task["id"])
        self.assertEqual(1, task["document_ids"].count(self.document["document_id"]))
        unknown = self.client.post(url, data={"document_id": "missing-id"})
        self.assertEqual(302, unknown.status_code)
        task = next(item for item in store.items("alice") if item["id"] == self.task["id"])
        self.assertNotIn("missing-id", task["document_ids"])
        anonymous = app.test_client()
        denied = anonymous.post(url, data={"document_id": self.document["document_id"]}, follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))

    def test_broken_reference_is_shown_as_unavailable(self):
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        store.update(self.task["id"], {"document_ids": ["gone-id"]}, "alice")
        board = self.client.get("/tasks/").get_data(as_text=True)
        self.assertIn("not accessible", board)

    def test_board_offers_link_picker(self):
        board = self.client.get("/tasks/").get_data(as_text=True)
        for marker in ("data-link-picker", "data-link-search", "data-link-results", "task_link_picker.js"):
            self.assertIn(marker, board)


if __name__ == "__main__":
    unittest.main()
