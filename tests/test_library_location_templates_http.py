"""Blackbox: nested storage locations from templates (Issue #1111, P1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore


class LibraryLocationTemplateHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "shelver", "password": "shelver-password-123"})
        self.client.post("/auth/login", data={"username": "shelver", "password": "shelver-password-123"})
        self.root = base / "documents"
        self.room = LibraryStore(self.root).create_location("Raum", "shelver")

    def tearDown(self):
        app.config.update(self.saved)

    def test_shelf_template_creates_nested_numbered_structure(self):
        response = self.client.post(
            "/library/locations/from-template",
            json={"template_id": "shelf", "name": "Regal 2",
                  "parent_id": self.room["location_id"], "count": 3},
        )
        self.assertEqual(201, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual("Raum / Regal 2", body["path"])
        self.assertEqual(["Fach 1", "Fach 2", "Fach 3"],
                         [child["name"] for child in body["children"]])
        resolve = self.client.get(
            "/library/locations/resolve",
            query_string={"code": body["children"][1]["code"]},
        )
        self.assertEqual("Raum / Regal 2 / Fach 2", resolve.get_json()["path"])

    def test_template_defaults_and_nesting_below_boxes(self):
        box = LibraryStore(self.root).create_location(
            "Kiste", "shelver", parent_id=self.room["location_id"])
        response = self.client.post(
            "/library/locations/from-template",
            json={"template_id": "drawer_unit", "name": "Kommode",
                  "parent_id": box["location_id"]},
        )
        self.assertEqual(201, response.status_code)
        body = response.get_json()
        self.assertEqual(4, len(body["children"]))
        self.assertEqual("Schublade 4", body["children"][3]["name"])
        resolve = self.client.get(
            "/library/locations/resolve",
            query_string={"code": body["children"][0]["code"]},
        )
        self.assertEqual("Raum / Kiste / Kommode / Schublade 1", resolve.get_json()["path"])

    def test_template_requests_are_validated(self):
        unknown = self.client.post(
            "/library/locations/from-template",
            json={"template_id": "spaceship", "name": "X"},
        )
        self.assertEqual(400, unknown.status_code)
        missing_name = self.client.post(
            "/library/locations/from-template", json={"template_id": "shelf", "name": "  "})
        self.assertEqual(400, missing_name.status_code)
        bad_count = self.client.post(
            "/library/locations/from-template",
            json={"template_id": "shelf", "name": "Regal X", "count": 99},
        )
        self.assertEqual(400, bad_count.status_code)
        bad_parent = self.client.post(
            "/library/locations/from-template",
            json={"template_id": "shelf", "name": "Regal X",
                  "parent_id": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertEqual(400, bad_parent.status_code)

    def test_template_endpoints_require_login(self):
        anonymous = app.test_client()
        denied = anonymous.post(
            "/library/locations/from-template",
            json={"template_id": "shelf", "name": "Regal X"}, follow_redirects=False,
        )
        self.assertIn(denied.status_code, (302, 303))
        catalog_denied = anonymous.get("/library/location-templates", follow_redirects=False)
        self.assertIn(catalog_denied.status_code, (302, 303))

    def test_template_catalog_and_form_are_visible(self):
        catalog = self.client.get("/library/location-templates")
        self.assertEqual(200, catalog.status_code)
        ids = [entry["template_id"] for entry in catalog.get_json()["templates"]]
        self.assertEqual(["shelf", "cabinet", "moving_box", "drawer_unit"], ids)
        page = self.client.get("/library")
        self.assertEqual(200, page.status_code)
        html = page.get_data(as_text=True)
        self.assertIn("data-library-template", html)
        self.assertIn("/library/locations/from-template", html)
        self.assertIn("Regal mit Fächern", html)


if __name__ == "__main__":
    unittest.main()
