"""HTTP contracts for resumable physical-library scan sessions."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g, session

from app.db import close_db
from app.library.routes import bp as library_blueprint
from app.library.store import LibraryStore
from app.object_store import ObjectStore
from app.security_controls import protect_browser_mutation


class LibraryScanSessionHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DATABASE=str(self.root / "test.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="library-scan-session-test",
        )
        with sqlite3.connect(self.app.config["DATABASE"]) as database:
            database.execute(
                """CREATE TABLE security_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    actor_id INTEGER,
                    actor_name TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '{}'
                )"""
            )
        self.app.teardown_appcontext(close_db)

        @self.app.before_request
        def authenticate_test_user():
            username = session.get("username")
            g.user = {
                "id": 1, "username": username, "is_admin": False, "is_disabled": False,
            } if username else None

        self.app.add_url_rule("/inventory", endpoint="inventory.index", view_func=lambda: "inventory")
        self.app.add_url_rule(
            "/inventory/item/<object_id>", endpoint="inventory.item_detail",
            view_func=lambda object_id: object_id,
        )
        self.app.before_request(protect_browser_mutation)
        self.app.register_blueprint(library_blueprint)
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["username"] = "reader"
            state["_csrf_token"] = "scan-session-csrf"
        self.location = LibraryStore(self.root).create_location("Regal A", "reader")
        self.book = ObjectStore(self.root).create(
            {"name": "A Book", "type": "book", "identifier": "9780306406157"},
            "reader",
        )

    def post_session(self, action, **values):
        return self.client.post(
            "/library/scan-session",
            data={"_csrf_token": "scan-session-csrf", "action": action, **values},
        )

    def start(self):
        response = self.post_session("start", location_id=self.location["location_id"])
        self.assertEqual(200, response.status_code)
        return response.json["session"]

    def assign(self, scan_session, code="9780306406157", scan_id="scan-1"):
        return self.client.post(
            "/library/assign",
            data={
                "_csrf_token": "scan-session-csrf",
                "book_code": code,
                "location_id": self.location["location_id"],
                "scan_session_id": scan_session["session_id"],
                "scan_id": scan_id,
            },
        )

    def test_session_survives_requests_and_records_assignment(self):
        scan_session = self.start()
        response = self.assign(scan_session)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json["scan_session"]["assigned"])

        restored = self.client.get("/library/scan-session")
        self.assertEqual(200, restored.status_code)
        self.assertEqual(scan_session["session_id"], restored.json["session"]["session_id"])
        self.assertEqual("no-store", restored.headers["Cache-Control"])

    def test_repeated_scan_request_is_idempotent(self):
        scan_session = self.start()
        self.assertEqual(200, self.assign(scan_session).status_code)
        repeated = self.assign(scan_session)
        self.assertEqual(200, repeated.status_code)
        self.assertTrue(repeated.json["replayed"])
        self.assertEqual(1, repeated.json["scan_session"]["total"])

    def test_missing_book_is_recorded_without_creating_an_object(self):
        scan_session = self.start()
        response = self.assign(scan_session, code="9780000000000")
        self.assertEqual(404, response.status_code)
        self.assertTrue(response.json["not_found"])
        self.assertEqual(1, response.json["scan_session"]["missing"])
        self.assertEqual(1, len(ObjectStore(self.root).objects("A Book")))

    def test_paused_session_rejects_assignment_before_mutation(self):
        scan_session = self.start()
        self.assertEqual(200, self.post_session("pause").status_code)
        response = self.assign(scan_session)
        self.assertEqual(409, response.status_code)
        self.assertIsNone(LibraryStore(self.root).assignment(self.book["object_id"]))
        self.assertEqual("active", self.post_session("resume").json["session"]["status"])

    def test_session_is_bound_to_authenticated_user(self):
        self.start()
        with self.client.session_transaction() as state:
            state["username"] = "other-reader"
        response = self.client.get("/library/scan-session")
        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.json["session"])

    def test_post_requires_csrf_and_finish_removes_progress(self):
        rejected = self.client.post(
            "/library/scan-session",
            data={"action": "start", "location_id": self.location["location_id"]},
        )
        self.assertEqual(403, rejected.status_code)
        self.start()
        self.assertEqual(200, self.post_session("finish").status_code)
        self.assertIsNone(self.client.get("/library/scan-session").json["session"])


if __name__ == "__main__":
    unittest.main()
