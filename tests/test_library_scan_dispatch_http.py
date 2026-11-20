"""Blackbox: scan dispatch for storing and removing objects (Issue #1111, P1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore
from app.object_store import ObjectStore


class ScanDispatchHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "scanner", "password": "scanner-password-123"})
        self.client.post("/auth/login", data={"username": "scanner", "password": "scanner-password-123"})
        self.root = base / "documents"
        store = LibraryStore(self.root)
        self.shelf = store.create_location("Regal", "scanner")
        self.item = ObjectStore(self.root).create(
            {"name": "Hammer", "type": "Werkzeug", "identifier": "HAM-7"}, "scanner")

    def tearDown(self):
        app.config.update(self.saved)

    def test_einlagern_assigns_to_target(self):
        response = self.client.post("/library/scan", json={
            "mode": "einlagern", "code": "HAM-7",
            "target": self.shelf["code"]})
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual("Regal", body["path"])
        assignment = LibraryStore(self.root).assignment(self.item["object_id"])
        self.assertEqual(self.shelf["location_id"], assignment["location_id"])

    def test_entnehmen_removes_assignment_and_mirror(self):
        LibraryStore(self.root).assign(self.item["object_id"], self.shelf["location_id"], "scanner")
        response = self.client.post("/library/scan", json={
            "mode": "entnehmen", "code": "HAM-7"})
        self.assertEqual(200, response.status_code)
        self.assertIsNone(LibraryStore(self.root).assignment(self.item["object_id"]))
        fresh = ObjectStore(self.root).object(self.item["object_id"])
        self.assertEqual("", fresh["location"])
        again = self.client.post("/library/scan", json={
            "mode": "entnehmen", "code": "HAM-7"})
        self.assertEqual(400, again.status_code)

    def test_scan_is_validated(self):
        bad_mode = self.client.post("/library/scan", json={
            "mode": "zaubern", "code": "HAM-7"})
        self.assertEqual(400, bad_mode.status_code)
        unknown_code = self.client.post("/library/scan", json={
            "mode": "einlagern", "code": "NOPE-1", "target": self.shelf["location_id"]})
        self.assertEqual(404, unknown_code.status_code)
        missing_target = self.client.post("/library/scan", json={
            "mode": "einlagern", "code": "HAM-7", "target": "LIB-L9999"})
        self.assertEqual(400, missing_target.status_code)

    def test_scan_requires_login_and_form_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post("/library/scan",
                                json={"mode": "entnehmen", "code": "HAM-7"},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        html = self.client.get("/library").get_data(as_text=True)
        self.assertIn("data-scan-dispatch", html)
        self.assertIn("/library/scan", html)
        self.assertIn("Entnehmen", html)


if __name__ == "__main__":
    unittest.main()
