"""Blackbox: warranty periods with expiry display (Issue #1111, P2)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import app
from app import db as database


class WarrantyHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "owner", "password": "owner-password-123"})
        self.client.post("/auth/login", data={"username": "owner", "password": "owner-password-123"})

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

    def test_active_warranty_shows_expiry(self):
        created = self.create_item(
            "Waschmaschine", purchase_date=date.today().isoformat(), warranty_months="24")
        self.assertEqual(200, created.status_code)
        html = self.detail_html(created.get_json()["object_id"])
        self.assertIn("data-warranty", html)
        self.assertIn("24 Monate", html)
        self.assertIn("aktiv", html)

    def test_expired_warranty_is_marked(self):
        created = self.create_item(
            "Toaster", purchase_date="2020-01-15", warranty_months="12")
        html = self.detail_html(created.get_json()["object_id"])
        self.assertIn("2021-01-15", html)
        self.assertIn("abgelaufen", html)

    def test_invalid_warranty_is_rejected(self):
        bad_text = self.create_item("Teil", warranty_months="lang")
        self.assertEqual(400, bad_text.status_code)
        self.assertEqual("invalid", bad_text.get_json()["fields"].get("warranty_months"))
        bad_range = self.create_item("Teil", warranty_months="999")
        self.assertEqual(400, bad_range.status_code)

    def test_value_display_stays_visible(self):
        created = self.create_item(
            "Fahrrad", market_price="299.99", currency="EUR", price_source="Händler")
        html = self.detail_html(created.get_json()["object_id"])
        self.assertIn("299.99 EUR", html)
        self.assertIn("Händler", html)

    def test_warranty_form_is_visible(self):
        html = self.client.get("/inventory").get_data(as_text=True)
        self.assertIn('name="warranty_months"', html)


if __name__ == "__main__":
    unittest.main()
