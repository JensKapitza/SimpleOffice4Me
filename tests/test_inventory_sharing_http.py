"""Blackbox: per-object friend-stock sharing opt-out (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.federation_inventory_index import build_inventory_rows

ISBN = "9780306406157"


class SharingFlagHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "sharer", "password": "sharer-password-123"})
        self.client.post("/auth/login", data={"username": "sharer", "password": "sharer-password-123"})
        self.root = base / "documents"

    def tearDown(self):
        app.config.update(self.saved)

    def create_item(self, title, barcode=ISBN, **fields):
        data = {"response_format": "json", "title": title,
                "item_type": "book", "barcode": barcode, **fields}
        response = self.client.post("/inventory/items", data=data)
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json().get("saved", True))
        return response.get_json()["object_id"]

    def detail_html(self, object_id):
        response = self.client.get(f"/inventory/{object_id}")
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_default_shared_and_opt_out_at_create(self):
        shared = self.create_item("Offenes Buch")
        self.assertIn("geteilt", self.detail_html(shared))
        self.assertEqual(1, len(build_inventory_rows(self.root)))
        private = self.create_item("Privates Buch", "9780306406164", share_private="1")
        html = self.detail_html(private)
        self.assertIn("privat", html)
        self.assertEqual(1, len(build_inventory_rows(self.root)))

    def test_toggle_changes_index_and_display(self):
        object_id = self.create_item("Buch")
        off = self.client.post(f"/inventory/{object_id}/sharing", json={"share_inventory": False})
        self.assertEqual(200, off.status_code)
        self.assertFalse(off.get_json()["share_inventory"])
        self.assertEqual([], build_inventory_rows(self.root))
        self.assertIn("privat", self.detail_html(object_id))
        on = self.client.post(f"/inventory/{object_id}/sharing", json={"share_inventory": True})
        self.assertTrue(on.get_json()["share_inventory"])
        self.assertEqual(1, len(build_inventory_rows(self.root)))

    def test_sharing_is_validated(self):
        object_id = self.create_item("Buch")
        bad = self.client.post(f"/inventory/{object_id}/sharing",
                               json={"share_inventory": "vielleicht"})
        self.assertEqual(400, bad.status_code)
        missing = self.client.post("/inventory/00000000-0000-0000-0000-000000000000/sharing",
                                   json={"share_inventory": True})
        self.assertEqual(404, missing.status_code)

    def test_sharing_requires_login_and_form_is_visible(self):
        object_id = self.create_item("Buch")
        anonymous = app.test_client()
        denied = anonymous.post(f"/inventory/{object_id}/sharing",
                                json={"share_inventory": False},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        self.assertIn("data-sharing", self.detail_html(object_id))
        index_html = self.client.get("/inventory").get_data(as_text=True)
        self.assertIn('name="share_private"', index_html)


if __name__ == "__main__":
    unittest.main()
