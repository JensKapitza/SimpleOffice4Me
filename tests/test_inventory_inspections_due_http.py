"""Blackbox: due maintenance inspections across objects (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app
from app import db as database


class DueInspectionsHttpTests(unittest.TestCase):
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
            "response_format": "json", "title": "Leiter", "item_type": "object",
        })
        self.assertEqual(200, created.status_code)
        self.object_id = created.get_json()["object_id"]

    def tearDown(self):
        app.config.update(self.saved)

    def add_inspection(self, name, due, **extra):
        data = {"inspection_name": name, "inspection_due": due,
                "inspection_responsible": "Ich", **extra}
        return self.client.post(f"/inventory/{self.object_id}/inspections", data=data)

    def test_due_list_contains_rule_sorted_first(self):
        soon = (date.today() + timedelta(days=2)).isoformat()
        later = (date.today() + timedelta(days=20)).isoformat()
        self.assertEqual(302, self.add_inspection("Spät", later).status_code)
        self.assertEqual(302, self.add_inspection("Bald", soon).status_code)
        body = self.client.get("/inventory/inspections/due",
                               headers={"Accept": "application/json"}).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(2, body["count"])
        self.assertEqual("Bald", body["due"][0]["name"])
        self.assertEqual("Leiter", body["due"][0]["object_name"])

    def test_horizon_filters_and_completed_rules_leave(self):
        far = (date.today() + timedelta(days=60)).isoformat()
        self.add_inspection("Fern", far)
        near = self.client.get("/inventory/inspections/due?days=7",
                               headers={"Accept": "application/json"}).get_json()
        self.assertEqual(0, near["count"])
        wide = self.client.get("/inventory/inspections/due?days=90",
                               headers={"Accept": "application/json"}).get_json()
        self.assertEqual(1, wide["count"])
        rule_id = wide["due"][0]["rule_id"]
        done = self.client.post(
            f"/inventory/{self.object_id}/inspections/{rule_id}/complete", data={})
        self.assertEqual(302, done.status_code)
        gone = self.client.get("/inventory/inspections/due?days=90",
                               headers={"Accept": "application/json"}).get_json()
        self.assertEqual(0, gone["count"])

    def test_due_page_renders_and_requires_login(self):
        self.add_inspection("Bald", (date.today() + timedelta(days=1)).isoformat())
        page = self.client.get("/inventory/inspections/due")
        self.assertEqual(200, page.status_code)
        html = page.get_data(as_text=True)
        self.assertIn("data-inspections-due", html)
        self.assertIn("Fällige Prüfungen (1)", html)
        self.assertIn("Leiter", html)
        anonymous = app.test_client()
        denied = anonymous.get("/inventory/inspections/due", follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        detail = self.client.get(f"/inventory/{self.object_id}").get_data(as_text=True)
        self.assertIn("Alle fälligen", detail)
        self.assertIn("/inventory/inspections/due", detail)


if __name__ == "__main__":
    unittest.main()
