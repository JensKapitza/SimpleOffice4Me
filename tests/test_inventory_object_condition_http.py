"""Blackbox: object lifecycle conditions (Issue #1111, P2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore


class ObjectConditionHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "keeper", "password": "keeper-password-123"})
        self.client.post("/auth/login", data={"username": "keeper", "password": "keeper-password-123"})
        self.root = base / "documents"

    def tearDown(self):
        app.config.update(self.saved)

    def create_item(self, title, **fields):
        data = {"response_format": "json", "title": title,
                "item_type": "object", **fields}
        return self.client.post("/inventory/items", data=data)

    def detail_html(self, object_id):
        response = self.client.get(f"/inventory/{object_id}")
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_condition_is_stored_and_shown(self):
        created = self.create_item("Bohrmaschine", condition="in_repair")
        self.assertEqual(200, created.status_code)
        html = self.detail_html(created.get_json()["object_id"])
        self.assertIn("data-condition", html)
        self.assertIn("In Reparatur", html)

    def test_condition_defaults_to_unset(self):
        created = self.create_item("Hammer")
        self.assertEqual(200, created.status_code)
        html = self.detail_html(created.get_json()["object_id"])
        self.assertIn("data-condition", html)
        self.assertNotIn("Defekt", html)

    def test_invalid_condition_is_rejected(self):
        response = self.create_item("Teil", condition="shiny")
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])

    def test_library_assignment_preserves_condition(self):
        created = self.create_item("Säge", condition="defective")
        object_id = created.get_json()["object_id"]
        room = LibraryStore(self.root).create_location("Raum", "keeper")
        assigned = self.client.post(
            f"/library/objects/{object_id}/care/location",
            data={"location_id": room["location_id"]},
        )
        self.assertEqual(302, assigned.status_code)
        self.assertIn("Defekt", self.detail_html(object_id))

    def test_condition_form_is_visible(self):
        html = self.client.get("/inventory").get_data(as_text=True)
        self.assertIn('name="condition"', html)
        self.assertIn("Ausgesondert", html)


if __name__ == "__main__":
    unittest.main()
