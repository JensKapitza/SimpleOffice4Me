"""Blackbox: minimum stock levels with low-stock warnings (Issue #1111, P3)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore


class MinStockHttpTests(unittest.TestCase):
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

    def create_item(self, title, **fields):
        data = {"response_format": "json", "title": title,
                "item_type": "object", **fields}
        return self.client.post("/inventory/items", data=data)

    def detail_html(self, object_id):
        response = self.client.get(f"/inventory/{object_id}")
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_low_stock_warns_on_detail_and_list(self):
        created = self.create_item("Schrauben", quantity="5", unit="Stück", min_stock="10")
        self.assertEqual(200, created.status_code)
        object_id = created.get_json()["object_id"]
        html = self.detail_html(object_id)
        self.assertIn("(mind. 10)", html)
        self.assertIn("nachbestellen", html)
        body = self.client.get("/inventory/low-stock").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(["Schrauben"], [row["name"] for row in body["items"]])

    def test_sufficient_stock_shows_no_warning(self):
        created = self.create_item("Nägel", quantity="50", unit="Stück", min_stock="10")
        html = self.detail_html(created.get_json()["object_id"])
        self.assertNotIn("nachbestellen", html)
        other = self.create_item("Dübel", quantity="50")
        self.assertNotIn("nachbestellen", self.detail_html(other.get_json()["object_id"]))
        body = self.client.get("/inventory/low-stock").get_json()
        self.assertEqual([], body["items"])

    def test_min_stock_is_validated(self):
        bad = self.create_item("Teil", quantity="5", min_stock="-1")
        self.assertEqual(400, bad.status_code)
        bad_text = self.create_item("Teil", quantity="5", min_stock="viel")
        self.assertEqual(400, bad_text.status_code)

    def test_assignment_preserves_min_stock(self):
        created = self.create_item("Schrauben", quantity="5", min_stock="10")
        object_id = created.get_json()["object_id"]
        room = LibraryStore(self.root).create_location("Raum", "stocker")
        assigned = self.client.post(
            f"/library/objects/{object_id}/care/location",
            data={"location_id": room["location_id"]},
        )
        self.assertEqual(302, assigned.status_code)
        self.assertIn("nachbestellen", self.detail_html(object_id))

    def test_low_stock_requires_login_and_form_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.get("/inventory/low-stock", follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        html = self.client.get("/inventory").get_data(as_text=True)
        self.assertIn('name="min_stock"', html)


if __name__ == "__main__":
    unittest.main()
