"""Blackbox: temporary locations with expected returns (Issue #1111, P2)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore
from app.object_store import ObjectStore


class TemporaryHoldHttpTests(unittest.TestCase):
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
        store = LibraryStore(self.root)
        self.bench = store.create_location("Werkbank", "keeper", temporary=True)
        self.shelf = store.create_location("Regal", "keeper")
        objects = ObjectStore(self.root)
        self.drill = objects.create(
            {"name": "Bohrmaschine", "type": "Werkzeug", "identifier": "BOH-9"}, "keeper")
        self.hammer = objects.create(
            {"name": "Hammer", "type": "Werkzeug", "identifier": "HAM-9"}, "keeper")
        self.due = (date.today() + timedelta(days=3)).isoformat()

    def tearDown(self):
        app.config.update(self.saved)

    def test_hold_lists_temporary_assignment_with_days_left(self):
        assigned = self.client.post(
            f"/library/objects/{self.drill['object_id']}/care/location",
            data={"location_id": self.bench["location_id"], "return_date": self.due},
        )
        self.assertEqual(302, assigned.status_code)
        LibraryStore(self.root).assign(self.hammer["object_id"], self.shelf["location_id"], "keeper")
        holds = self.client.get("/library/temporary-holds").get_json()
        self.assertTrue(holds["ok"])
        self.assertEqual(1, holds["count"])
        self.assertEqual(0, holds["overdue"])
        hold = holds["holds"][0]
        self.assertEqual("Werkbank", hold["location_name"])
        self.assertEqual(self.due, hold["return_date"])
        self.assertEqual(3, hold["days_left"])
        self.assertEqual("Bohrmaschine", hold["name"])

    def test_overdue_and_dateless_holds_are_listed(self):
        store = LibraryStore(self.root)
        store.assign(self.drill["object_id"], self.bench["location_id"], "keeper",
                     return_date=date.today().isoformat())
        holds = self.client.get("/library/temporary-holds",
                                query_string={"today": (date.today() + timedelta(days=2)).isoformat()})
        body = holds.get_json()
        self.assertEqual(1, body["count"])
        self.assertEqual(1, body["overdue"])
        self.assertEqual(-2, body["holds"][0]["days_left"])

    def test_return_dates_are_validated(self):
        past = self.client.post(
            f"/library/objects/{self.drill['object_id']}/care/location",
            data={"location_id": self.bench["location_id"], "return_date": "2000-01-01"},
        )
        self.assertEqual(302, past.status_code)
        holds = self.client.get("/library/temporary-holds").get_json()
        self.assertEqual(0, holds["count"])

    def test_holds_require_login_and_ui_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.get("/library/temporary-holds", follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        LibraryStore(self.root).assign(self.drill["object_id"], self.bench["location_id"],
                                       "keeper", return_date=self.due)
        html = self.client.get("/library").get_data(as_text=True)
        self.assertIn("data-temporary-holds", html)
        self.assertIn("Temporäre Auslagen (1)", html)
        self.assertIn("temporär", html)
        care_html = self.client.get(
            f"/library/objects/{self.drill['object_id']}/care").get_data(as_text=True)
        self.assertIn("Rückgabe bis", care_html)


if __name__ == "__main__":
    unittest.main()
