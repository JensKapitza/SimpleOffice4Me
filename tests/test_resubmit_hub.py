"""Resubmit hub: create from documents, due-date views, comments and history."""
from __future__ import annotations

import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.todo_store import TodoStore


class ResubmitHubTests(unittest.TestCase):
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
        self.document = store.import_upload(io.BytesIO(b"resubmit me"), "vertrag.txt", "alice")

    def tearDown(self):
        app.config.update(self.saved)

    def _create_resubmit(self, title="WV Rechnung", due=None, note="Bitte prüfen"):
        return self.client.post(
            f"/documents/{self.document['document_id']}/tasks",
            data={"title": title, "description": note, "due": due or date.today().isoformat(),
                  "categories": "Wiedervorlage"},
            follow_redirects=False,
        )

    def test_resubmit_from_document_links_source_and_due_date(self):
        response = self._create_resubmit(due="2026-12-24")
        self.assertEqual(302, response.status_code)
        board = self.client.get("/tasks/").get_data(as_text=True)
        self.assertIn("WV Rechnung", board)
        self.assertIn("2026-12-24", board)
        self.assertIn("Open source file", board)

    def test_due_date_views_filter_tasks(self):
        today = date.today()
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        store.add("WV heute", "alice", {"due": today.isoformat(), "categories": ["Wiedervorlage"]})
        store.add("WV überfällig", "alice", {"due": (today - timedelta(days=2)).isoformat()})
        store.add("WV woche", "alice", {"due": (today + timedelta(days=3)).isoformat()})
        done = store.add("WV fertig", "alice", {"due": today.isoformat()})
        store.update(done["id"], {"status": "completed"}, "alice")
        waiting = store.add("WV wartend", "alice", {})
        store.update(waiting["id"], {"status": "in-process"}, "alice")
        cases = {
            "today": ("WV heute", "WV überfällig"),
            "overdue": ("WV überfällig", "WV heute"),
            "week": ("WV woche", "WV überfällig"),
            "waiting": ("WV wartend", "WV heute"),
            "done": ("WV fertig", "WV heute"),
        }
        for view, (present, absent) in cases.items():
            with self.subTest(view=view):
                html = self.client.get(f"/tasks/?view={view}").get_data(as_text=True)
                self.assertIn(present, html)
                self.assertNotIn(absent, html)
        unknown = self.client.get("/tasks/?view=nonsense").get_data(as_text=True)
        self.assertIn("WV heute", unknown)

    def test_comment_and_history_are_visible(self):
        self._create_resubmit()
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        task = next(item for item in store.items("alice") if item["title"] == "WV Rechnung")
        response = self.client.post(
            f"/tasks/{task['id']}/comments", data={"text": "Lieferant anrufen"}, follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        board = self.client.get("/tasks/").get_data(as_text=True)
        self.assertIn("Lieferant anrufen", board)
        self.assertIn("todo_created", board)
        self.assertIn("todo_comment_added", board)

    def test_delegate_assigns_task(self):
        self._create_resubmit()
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        task = next(item for item in store.items("alice") if item["title"] == "WV Rechnung")
        response = self.client.post(
            f"/tasks/{task['id']}/update", data={"assigned_to": "bob"}, follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        updated = next(item for item in store.items("alice") if item["id"] == task["id"])
        self.assertIn("bob", updated.get("assigned_to", []))


if __name__ == "__main__":
    unittest.main()
