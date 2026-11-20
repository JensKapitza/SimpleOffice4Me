"""Blackbox: barcode and QR location labels (Issue #1111, P1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class LocationLabelHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "labeler", "password": "labeler-password-123"})
        self.client.post("/auth/login", data={"username": "labeler", "password": "labeler-password-123"})
        self.location = LibraryStore(base / "documents").create_location("Regal 1", "labeler")

    def tearDown(self):
        app.config.update(self.saved)

    def preview(self, style):
        return self.client.post(
            f"/library/locations/{self.location['location_id']}/labels.preview",
            data={"label_style": style},
        )

    def test_preview_renders_barcode_and_qr_png(self):
        for style in ("barcode", "qr"):
            response = self.preview(style)
            self.assertEqual(200, response.status_code, style)
            self.assertEqual("image/png", response.mimetype)
            self.assertTrue(response.data.startswith(PNG_MAGIC), style)
            self.assertGreater(len(response.data), 1000, style)
            self.assertEqual("no-store", response.headers.get("Cache-Control"))

    def test_preview_validates_style_and_location(self):
        bad_style = self.preview("aztec")
        self.assertEqual(400, bad_style.status_code)
        missing = self.client.post(
            "/library/locations/00000000-0000-0000-0000-000000000000/labels.preview",
            data={"label_style": "qr"})
        self.assertEqual(404, missing.status_code)

    def test_preview_requires_login(self):
        anonymous = app.test_client()
        denied = anonymous.post(
            f"/library/locations/{self.location['location_id']}/labels.preview",
            data={"label_style": "qr"}, follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))

    def test_label_form_offers_style_and_preview(self):
        html = self.client.get("/library").get_data(as_text=True)
        self.assertIn("data-location-label", html)
        self.assertIn('name="label_style"', html)
        self.assertIn("labels.preview", html)
        self.assertIn("Vorschau", html)

    def test_qr_payload_resolves_via_location_api(self):
        resolve = self.client.get(
            "/library/locations/resolve", query_string={"code": self.location["code"]})
        self.assertEqual(200, resolve.status_code)
        body = resolve.get_json()
        self.assertTrue(body["found"])
        self.assertEqual(self.location["location_id"], body["location"]["location_id"])


if __name__ == "__main__":
    unittest.main()
