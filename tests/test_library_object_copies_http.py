"""Blackbox: separately trackable copies for any object type (Issue #1111, P1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore


class ObjectCopyHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "cloner", "password": "cloner-password-123"})
        self.client.post("/auth/login", data={"username": "cloner", "password": "cloner-password-123"})
        self.root = base / "documents"

    def tearDown(self):
        app.config.update(self.saved)

    def create_item(self, title, item_type="PC", **fields):
        response = self.client.post("/inventory/items", data={
            "response_format": "json", "title": title, "item_type": item_type, **fields,
        })
        self.assertEqual(200, response.status_code)
        return response.get_json()["object_id"]

    def test_two_pcs_as_copies_at_different_locations(self):
        first = self.create_item("Büro-PC", serial_number="PC-001")
        copied = self.client.post(
            f"/library/objects/{first}/copies", headers={"Accept": "application/json"})
        self.assertEqual(201, copied.status_code)
        second = copied.get_json()["object_id"]
        self.assertNotEqual(first, second)
        store = LibraryStore(self.root)
        room_a = store.create_location("Raum A", "cloner")
        room_b = store.create_location("Raum B", "cloner")
        for object_id, room in ((first, room_a), (second, room_b)):
            assigned = self.client.post(
                f"/library/objects/{object_id}/care/location",
                data={"location_id": room["location_id"]},
            )
            self.assertEqual(302, assigned.status_code)
        listing = self.client.get(f"/library/objects/{first}/copies")
        self.assertEqual(200, listing.status_code)
        body = listing.get_json()
        self.assertEqual(2, body["count"])
        self.assertEqual({first, second}, {row["object_id"] for row in body["copies"]})
        detail = self.client.get(f"/inventory/{first}")
        html = detail.get_data(as_text=True)
        self.assertIn("Exemplare (2)", html)
        self.assertIn("Raum A", html)
        self.assertIn("Raum B", html)

    def test_serial_numbers_are_not_inherited(self):
        first = self.create_item("Messgerät", "Gerät", serial_number="SN-777")
        copied = self.client.post(
            f"/library/objects/{first}/copies", headers={"Accept": "application/json"})
        second = copied.get_json()["object_id"]
        source_html = self.client.get(f"/inventory/{first}").get_data(as_text=True)
        copy_html = self.client.get(f"/inventory/{second}").get_data(as_text=True)
        self.assertIn("SN-777", source_html)
        self.assertNotIn("SN-777", copy_html)

    def test_unknown_source_is_rejected(self):
        missing = "00000000-0000-0000-0000-000000000000"
        listed = self.client.get(f"/library/objects/{missing}/copies")
        self.assertEqual(404, listed.status_code)
        created = self.client.post(
            f"/library/objects/{missing}/copies", headers={"Accept": "application/json"})
        self.assertEqual(400, created.status_code)
        self.assertFalse(created.get_json()["ok"])

    def test_copy_endpoints_require_login(self):
        anonymous = app.test_client()
        listed = anonymous.get("/library/objects/x/copies", follow_redirects=False)
        self.assertIn(listed.status_code, (302, 303))
        created = anonymous.post("/library/objects/x/copies", follow_redirects=False)
        self.assertIn(created.status_code, (302, 303))

    def test_detail_page_offers_copy_creation(self):
        first = self.create_item("Büro-PC")
        html = self.client.get(f"/inventory/{first}").get_data(as_text=True)
        self.assertIn("data-object-copies", html)
        self.assertIn("Weiteres Exemplar anlegen", html)
        self.assertIn(f"/library/objects/{first}/copies", html)


if __name__ == "__main__":
    unittest.main()
