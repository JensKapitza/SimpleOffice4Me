"""Blackbox: object reservations with date ranges (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app
from app import db as database


class ReservationHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "booker", "password": "booker-password-123"})
        self.client.post("/auth/login", data={"username": "booker", "password": "booker-password-123"})
        created = self.client.post("/inventory/items", data={
            "response_format": "json", "title": "Beamer", "item_type": "object",
        })
        self.assertEqual(200, created.status_code)
        self.object_id = created.get_json()["object_id"]
        self.start = (date.today() + timedelta(days=1)).isoformat()
        self.end = (date.today() + timedelta(days=3)).isoformat()

    def tearDown(self):
        app.config.update(self.saved)

    def reserve(self, reserver="Team", start=None, end=None):
        return self.client.post(
            f"/inventory/{self.object_id}/reservations",
            json={"reserver": reserver, "start_date": start or self.start,
                  "end_date": end or self.end})

    def test_reserve_and_cancel_cycle(self):
        made = self.reserve()
        self.assertEqual(201, made.status_code)
        reservation_id = made.get_json()["reservation"]["reservation_id"]
        detail = self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)
        self.assertIn("Team", detail)
        self.assertIn(self.start, detail)
        cancelled = self.client.post(
            f"/inventory/reservations/{reservation_id}/cancel", json={})
        self.assertEqual(200, cancelled.status_code)
        detail = self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)
        self.assertIn("Keine aktiven Reservierungen", detail)
        again = self.client.post(
            f"/inventory/reservations/{reservation_id}/cancel", json={})
        self.assertEqual(400, again.status_code)

    def test_overlapping_reservations_are_rejected(self):
        self.assertEqual(201, self.reserve("A").status_code)
        overlap = self.reserve("B", start=self.start, end=self.end)
        self.assertEqual(400, overlap.status_code)
        self.assertIn("überschneidet", overlap.get_json()["error"])
        later_start = (date.today() + timedelta(days=5)).isoformat()
        later_end = (date.today() + timedelta(days=6)).isoformat()
        self.assertEqual(201, self.reserve("B", start=later_start, end=later_end).status_code)

    def test_reservation_dates_are_validated(self):
        past = self.reserve("A", start="2000-01-01", end="2000-01-02")
        self.assertEqual(400, past.status_code)
        inverted = self.reserve("A", start=self.end, end=self.start)
        self.assertEqual(400, inverted.status_code)
        missing_name = self.reserve("  ")
        self.assertEqual(400, missing_name.status_code)
        unknown = self.client.post(
            "/inventory/00000000-0000-0000-0000-000000000000/reservations",
            json={"reserver": "A", "start_date": self.start, "end_date": self.end})
        self.assertEqual(404, unknown.status_code)

    def test_reservations_require_login_and_ui_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post(f"/inventory/{self.object_id}/reservations",
                                json={"reserver": "x", "start_date": self.start,
                                      "end_date": self.end},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        html = self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)
        self.assertIn("data-object-reservations", html)
        self.assertIn("Reservierungen", html)


if __name__ == "__main__":
    unittest.main()
