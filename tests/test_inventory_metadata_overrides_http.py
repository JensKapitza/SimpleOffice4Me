"""Blackbox: manual metadata values beside system values (Issue #1111, P1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database


class MetadataOverrideHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "editor", "password": "editor-password-123"})
        self.client.post("/auth/login", data={"username": "editor", "password": "editor-password-123"})
        created = self.client.post("/inventory/items", data={
            "response_format": "json", "title": "Fachbuch", "item_type": "book",
            "barcode": "9783000000025", "manufacturer": "System Hersteller",
        })
        self.assertEqual(200, created.status_code)
        self.object_id = created.get_json()["object_id"]

    def tearDown(self):
        app.config.update(self.saved)

    def detail_html(self):
        response = self.client.get(f"/inventory/{self.object_id}")
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_manual_and_system_values_are_both_visible(self):
        response = self.client.post(
            f"/inventory/{self.object_id}/overrides",
            json={"field": "manufacturer", "value": "Manueller Hersteller"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("Manueller Hersteller",
                         response.get_json()["overrides"]["manufacturer"]["value"])
        html = self.detail_html()
        self.assertIn("metadata-manual", html)
        self.assertIn("Manueller Hersteller", html)
        self.assertIn("System: System Hersteller", html)

    def test_clear_restores_system_value(self):
        self.client.post(f"/inventory/{self.object_id}/overrides",
                         json={"field": "manufacturer", "value": "Manueller Hersteller"})
        cleared = self.client.post(f"/inventory/{self.object_id}/overrides/clear",
                                   json={"field": "manufacturer"})
        self.assertEqual(200, cleared.status_code)
        self.assertEqual({}, cleared.get_json()["overrides"])
        html = self.detail_html()
        self.assertNotIn("Manueller Hersteller", html)
        self.assertIn("System Hersteller", html)

    def test_overrides_are_validated(self):
        bad_field = self.client.post(
            f"/inventory/{self.object_id}/overrides",
            json={"field": "Bad Field!", "value": "x"})
        self.assertEqual(400, bad_field.status_code)
        empty_value = self.client.post(
            f"/inventory/{self.object_id}/overrides",
            json={"field": "manufacturer", "value": "  "})
        self.assertEqual(400, empty_value.status_code)
        too_long = self.client.post(
            f"/inventory/{self.object_id}/overrides",
            json={"field": "manufacturer", "value": "x" * 2001})
        self.assertEqual(400, too_long.status_code)
        missing = self.client.post(
            "/inventory/00000000-0000-0000-0000-000000000000/overrides",
            json={"field": "manufacturer", "value": "x"})
        self.assertEqual(404, missing.status_code)
        no_override = self.client.post(
            f"/inventory/{self.object_id}/overrides/clear",
            json={"field": "publisher"})
        self.assertEqual(400, no_override.status_code)

    def test_override_endpoints_require_login(self):
        anonymous = app.test_client()
        denied = anonymous.post(f"/inventory/{self.object_id}/overrides",
                                json={"field": "a", "value": "b"},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))

    def test_override_form_is_visible_on_detail_page(self):
        html = self.detail_html()
        self.assertIn("data-metadata-overrides", html)
        self.assertIn(f"/inventory/{self.object_id}/overrides", html)
        self.assertIn("Manuelle Metadaten", html)


if __name__ == "__main__":
    unittest.main()
