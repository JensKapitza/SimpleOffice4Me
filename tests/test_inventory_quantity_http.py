"""Blackbox: bulk quantities vs single pieces (Issue #1111, P1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore


class InventoryQuantityHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "stocker", "password": "stocker-password-123"})
        self.client.post("/auth/login", data={"username": "stocker", "password": "stocker-password-123"})
        self.root = base / "documents"

    def tearDown(self):
        app.config.update(self.saved)

    def create_item(self, **fields):
        data = {"response_format": "json", "title": "Schrauben M4",
                "item_type": "object", **fields}
        return self.client.post("/inventory/items", data=data)

    def detail_html(self, object_id):
        response = self.client.get(f"/inventory/{object_id}")
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_bulk_item_shows_quantity_and_unit(self):
        created = self.create_item(quantity="50", unit="Stück")
        self.assertEqual(200, created.status_code)
        body = created.get_json()
        self.assertTrue(body["ok"])
        html = self.detail_html(body["object_id"])
        self.assertIn("data-stock", html)
        self.assertIn("50 Stück", html)

    def test_single_piece_without_quantity(self):
        created = self.create_item(title="Akkuschrauber", serial_number="SN-123")
        self.assertEqual(200, created.status_code)
        html = self.detail_html(created.get_json()["object_id"])
        self.assertIn("Einzelstück", html)
        self.assertIn("SN-123", html)

    def test_invalid_quantities_are_rejected(self):
        for bad in ("-1", "abc", "1000000000"):
            response = self.create_item(title=f"Teil {bad}", quantity=bad)
            self.assertEqual(400, response.status_code, bad)
            self.assertFalse(response.get_json()["ok"])
        too_long_unit = self.create_item(title="Teil", quantity="2", unit="x" * 25)
        self.assertEqual(400, too_long_unit.status_code)

    def test_assign_and_copy_preserve_source_quantity(self):
        created = self.client.post("/inventory/items", data={
            "response_format": "json", "title": "Fachbuch", "item_type": "book",
            "barcode": "9783000000018", "quantity": "2", "unit": "Stück",
        })
        self.assertEqual(200, created.status_code)
        object_id = created.get_json()["object_id"]
        room = LibraryStore(self.root).create_location("Raum", "stocker")
        assigned = self.client.post(
            f"/library/objects/{object_id}/care/location",
            data={"location_id": room["location_id"]},
        )
        self.assertEqual(302, assigned.status_code)
        self.assertIn("2 Stück", self.detail_html(object_id))
        copied = self.client.post(
            f"/library/books/{object_id}/copies", headers={"Accept": "application/json"})
        self.assertEqual(201, copied.status_code)
        copy_id = copied.get_json()["object_id"]
        self.assertIn("Einzelstück", self.detail_html(copy_id))
        self.assertIn("2 Stück", self.detail_html(object_id))


if __name__ == "__main__":
    unittest.main()
