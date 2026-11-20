"""Blackbox: physical object checkout, return and due dates (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app
from app import db as database


class LendingHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "lender", "password": "lender-password-123"})
        self.client.post("/auth/login", data={"username": "lender", "password": "lender-password-123"})
        created = self.client.post("/inventory/items", data={
            "response_format": "json", "title": "Bohrmaschine", "item_type": "object",
        })
        self.assertEqual(200, created.status_code)
        self.object_id = created.get_json()["object_id"]
        self.due = (date.today() + timedelta(days=7)).isoformat()

    def tearDown(self):
        app.config.update(self.saved)

    def test_checkout_and_return_cycle(self):
        checkout = self.client.post(
            f"/inventory/{self.object_id}/loans",
            json={"borrower": "Nachbar", "due_date": self.due, "note": "Gartenhaus"})
        self.assertEqual(201, checkout.status_code)
        loan_id = checkout.get_json()["loan"]["loan_id"]
        detail = self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)
        self.assertIn("Entliehen an", detail)
        self.assertIn("Nachbar", detail)
        listing = self.client.get("/inventory/loans").get_json()
        self.assertEqual(1, len(listing["open"]))
        returned = self.client.post(f"/inventory/loans/{loan_id}/return", json={})
        self.assertEqual(200, returned.status_code)
        self.assertTrue(returned.get_json()["loan"]["returned_at"])
        listing = self.client.get("/inventory/loans").get_json()
        self.assertEqual([], listing["open"])
        detail = self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)
        self.assertIn("(zurück", detail)

    def test_double_checkout_is_rejected(self):
        first = self.client.post(
            f"/inventory/{self.object_id}/loans",
            json={"borrower": "A", "due_date": self.due})
        self.assertEqual(201, first.status_code)
        second = self.client.post(
            f"/inventory/{self.object_id}/loans",
            json={"borrower": "B", "due_date": self.due})
        self.assertEqual(400, second.status_code)
        self.assertIn("bereits entliehen", second.get_json()["error"])

    def test_lending_is_validated(self):
        past = self.client.post(
            f"/inventory/{self.object_id}/loans",
            json={"borrower": "A", "due_date": "2000-01-01"})
        self.assertEqual(400, past.status_code)
        missing_borrower = self.client.post(
            f"/inventory/{self.object_id}/loans",
            json={"borrower": "  ", "due_date": self.due})
        self.assertEqual(400, missing_borrower.status_code)
        unknown_object = self.client.post(
            "/inventory/00000000-0000-0000-0000-000000000000/loans",
            json={"borrower": "A", "due_date": self.due})
        self.assertEqual(404, unknown_object.status_code)
        unknown_loan = self.client.post(
            "/inventory/loans/00000000-0000-0000-0000-000000000000/return", json={})
        self.assertEqual(400, unknown_loan.status_code)

    def test_lending_requires_login_and_ui_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post(f"/inventory/{self.object_id}/loans",
                                json={"borrower": "x", "due_date": self.due},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        html = self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)
        self.assertIn("data-object-lending", html)
        self.assertIn("Verleih", html)
        self.assertIn("Entleihen", html)


if __name__ == "__main__":
    unittest.main()
