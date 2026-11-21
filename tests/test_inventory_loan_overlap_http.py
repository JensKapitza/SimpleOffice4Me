"""Blackbox: loan/reservation overlap checks block without auto-cancel (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app
from app import db as database


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


class LoanOverlapHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "overlap", "password": "overlap-password-123"})
        self.client.post("/auth/login", data={"username": "overlap", "password": "overlap-password-123"})
        created = self.client.post("/inventory/items", data={
            "response_format": "json", "title": "Leiter", "item_type": "object",
        })
        self.assertEqual(200, created.status_code)
        self.object_id = created.get_json()["object_id"]

    def tearDown(self):
        app.config.update(self.saved)

    def checkout(self, borrower="Gast", due=None):
        return self.client.post(
            f"/inventory/{self.object_id}/loans",
            json={"borrower": borrower, "due_date": due or day(7)})

    def reserve(self, reserver="Team", start=None, end=None):
        return self.client.post(
            f"/inventory/{self.object_id}/reservations",
            json={"reserver": reserver, "start_date": start or day(1),
                  "end_date": end or day(3)})

    def test_checkout_blocked_by_overlapping_reservation(self):
        self.assertEqual(201, self.reserve().status_code)
        denied = self.checkout()
        self.assertEqual(400, denied.status_code)
        self.assertIn("reserviert", denied.get_json()["error"])
        listing = self.client.get("/inventory/loans").get_json()
        self.assertEqual([], listing["open"])

    def test_checkout_allowed_outside_reservation_window(self):
        self.assertEqual(201, self.reserve(start=day(10), end=day(12)).status_code)
        self.assertEqual(201, self.checkout(due=day(7)).status_code)

    def test_cancelled_reservation_unblocks_checkout(self):
        made = self.reserve()
        reservation_id = made.get_json()["reservation"]["reservation_id"]
        self.assertEqual(200, self.client.post(
            f"/inventory/reservations/{reservation_id}/cancel", json={}).status_code)
        self.assertEqual(201, self.checkout().status_code)

    def test_reserve_blocked_by_open_loan(self):
        self.assertEqual(201, self.checkout().status_code)
        denied = self.reserve()
        self.assertEqual(400, denied.status_code)
        self.assertIn("entliehen", denied.get_json()["error"])

    def test_reserve_allowed_after_return(self):
        loan_id = self.checkout().get_json()["loan"]["loan_id"]
        self.assertEqual(200, self.client.post(
            f"/inventory/loans/{loan_id}/return", json={}).status_code)
        made = self.reserve()
        self.assertEqual(201, made.status_code)


if __name__ == "__main__":
    unittest.main()
