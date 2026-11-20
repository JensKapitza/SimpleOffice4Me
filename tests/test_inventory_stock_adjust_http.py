"""Blackbox: booking stock changes with reasons (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database


class StockAdjustHttpTests(unittest.TestCase):
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
        created = self.client.post("/inventory/items", data={
            "response_format": "json", "title": "Schrauben", "item_type": "object",
            "quantity": "50", "unit": "Stück",
        })
        self.assertEqual(200, created.status_code)
        self.object_id = created.get_json()["object_id"]

    def tearDown(self):
        app.config.update(self.saved)

    def adjust(self, **fields):
        return self.client.post(f"/inventory/{self.object_id}/stock", json=fields)

    def detail_html(self):
        return self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)

    def test_booking_changes_quantity_with_reason(self):
        booked = self.adjust(delta="-8", reason="Werkstatt")
        self.assertEqual(200, booked.status_code)
        body = booked.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual("42", body["quantity"])
        self.assertEqual("Werkstatt", body["reason"])
        self.assertIn("42 Stück", self.detail_html())
        restock = self.adjust(delta="+3,5", reason="Einkauf")
        self.assertEqual("45.5", restock.get_json()["quantity"])

    def test_booking_is_validated(self):
        negative = self.adjust(delta="-100", reason="Test")
        self.assertEqual(400, negative.status_code)
        self.assertIn("negativ", negative.get_json()["error"])
        self.assertIn("50 Stück", self.detail_html())
        bad_number = self.adjust(delta="viel", reason="Test")
        self.assertEqual(400, bad_number.status_code)
        missing_reason = self.adjust(delta="-1", reason="  ")
        self.assertEqual(400, missing_reason.status_code)
        unknown = self.client.post("/inventory/00000000-0000-0000-0000-000000000000/stock",
                                   json={"delta": "-1", "reason": "Test"})
        self.assertEqual(404, unknown.status_code)

    def test_single_pieces_cannot_be_adjusted(self):
        created = self.client.post("/inventory/items", data={
            "response_format": "json", "title": "Hammer", "item_type": "object",
        })
        object_id = created.get_json()["object_id"]
        response = self.client.post(f"/inventory/{object_id}/stock",
                                    json={"delta": "-1", "reason": "Test"})
        self.assertEqual(400, response.status_code)
        self.assertIn("Einzelstück", self.client.get(f"/inventory/{object_id}").get_data(as_text=True))

    def test_booking_requires_login_and_form_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post(f"/inventory/{self.object_id}/stock",
                                json={"delta": "-1", "reason": "x"},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        html = self.detail_html()
        self.assertIn("data-stock-adjust", html)
        self.assertIn(f"/inventory/{self.object_id}/stock", html)


if __name__ == "__main__":
    unittest.main()
