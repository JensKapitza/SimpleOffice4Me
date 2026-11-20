"""Blackbox: location stock counts with difference reports (Issue #1111, P2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore
from app.object_store import ObjectStore


class LibraryInventoryCountHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "counter", "password": "counter-password-123"})
        self.client.post("/auth/login", data={"username": "counter", "password": "counter-password-123"})
        self.root = base / "documents"
        store = LibraryStore(self.root)
        self.box = store.create_location("Kiste", "counter")
        self.shelf = store.create_location("Regal", "counter")
        objects = ObjectStore(self.root)
        self.present = objects.create(
            {"name": "Hammer", "type": "Werkzeug", "identifier": "HAM-1"}, "counter")
        self.missing = objects.create(
            {"name": "Zange", "type": "Werkzeug", "identifier": "ZAN-1"}, "counter")
        self.away = objects.create(
            {"name": "Bohrer", "type": "Werkzeug", "identifier": "BOH-1"}, "counter")
        store.assign(self.present["object_id"], self.box["location_id"], "counter")
        store.assign(self.missing["object_id"], self.box["location_id"], "counter")
        store.assign(self.away["object_id"], self.shelf["location_id"], "counter")

    def tearDown(self):
        app.config.update(self.saved)

    def test_count_reports_present_missing_misplaced_unknown(self):
        response = self.client.post("/library/locations/inventory", json={
            "location_id": self.box["location_id"],
            "codes": ["HAM-1", "BOH-1", "NOPE-9", "HAM-1"],
        })
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(
            {"scanned": 2, "present": 1, "missing": 1, "misplaced": 1, "unknown": 1},
            body["summary"],
        )
        self.assertEqual(["HAM-1"], [row["identifier"] for row in body["present"]])
        self.assertEqual(["ZAN-1"], [row["identifier"] for row in body["missing"]])
        self.assertEqual(["BOH-1"], [row["identifier"] for row in body["misplaced"]])
        self.assertEqual("Regal", body["misplaced"][0]["expected_path"])
        self.assertEqual(["NOPE-9"], body["unknown"])

    def test_count_is_recorded_in_event_trail(self):
        counted = self.client.post("/library/locations/inventory", json={
            "location_id": self.box["location_id"], "codes": ["HAM-1"],
        })
        self.assertEqual(200, counted.status_code)
        page = self.client.get("/library")
        html = page.get_data(as_text=True)
        self.assertIn("inventory_counted", html)
        self.assertIn(self.box["code"], html)

    def test_count_validates_input_and_location(self):
        empty = self.client.post("/library/locations/inventory", json={
            "location_id": self.box["location_id"], "codes": [],
        })
        self.assertEqual(400, empty.status_code)
        too_many = self.client.post("/library/locations/inventory", json={
            "location_id": self.box["location_id"], "codes": ["x"] * 501,
        })
        self.assertEqual(400, too_many.status_code)
        unknown = self.client.post("/library/locations/inventory", json={
            "location_id": "00000000-0000-0000-0000-000000000000", "codes": ["HAM-1"],
        })
        self.assertEqual(404, unknown.status_code)

    def test_count_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.post("/library/locations/inventory",
                                  json={"location_id": "x", "codes": ["y"]},
                                  follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))

    def test_count_form_is_visible_on_library_page(self):
        page = self.client.get("/library")
        self.assertEqual(200, page.status_code)
        html = page.get_data(as_text=True)
        self.assertIn("data-library-inventory", html)
        self.assertIn("/library/locations/inventory", html)
        self.assertIn("Bestand zählen", html)


if __name__ == "__main__":
    unittest.main()
