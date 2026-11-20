"""Blackbox: printable location label sheets (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore


class LocationSheetHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "press", "password": "press-password-123"})
        self.client.post("/auth/login", data={"username": "press", "password": "press-password-123"})
        store = LibraryStore(base / "documents")
        self.first = store.create_location("Regal 1", "press")
        self.second = store.create_location("Kiste 2", "press")

    def tearDown(self):
        app.config.update(self.saved)

    def sheet(self, **fields):
        data = {"location_id": [self.first["location_id"], self.second["location_id"]],
                "barcode_style": "qr_with_text", "label_layout": "a4_3x7"}
        data.update(fields)
        return self.client.post("/library/locations/labels.pdf", data=data)

    def test_sheet_downloads_valid_pdf(self):
        response = self.sheet()
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.mimetype)
        self.assertTrue(response.data.startswith(b"%PDF"))
        self.assertGreater(len(response.data), 2000)
        media = self.sheet(label_layout="media", barcode_style="with_text")
        self.assertTrue(media.data.startswith(b"%PDF"))

    def test_sheet_selection_is_validated(self):
        empty = self.client.post("/library/locations/labels.pdf", data={
            "barcode_style": "qr_with_text", "label_layout": "a4_3x7"})
        self.assertEqual(400, empty.status_code)
        bad_style = self.sheet(barcode_style="aztec")
        self.assertEqual(400, bad_style.status_code)
        bad_layout = self.sheet(label_layout="a3")
        self.assertEqual(400, bad_layout.status_code)
        unknown = self.client.post("/library/locations/labels.pdf", data={
            "location_id": "00000000-0000-0000-0000-000000000000",
            "barcode_style": "qr_with_text", "label_layout": "a4_3x7"})
        self.assertEqual(400, unknown.status_code)

    def test_sheet_requires_login_and_form_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post("/library/locations/labels.pdf",
                                data={"location_id": self.first["location_id"]},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        html = self.client.get("/library").get_data(as_text=True)
        self.assertIn("data-location-sheet", html)
        self.assertIn("/library/locations/labels.pdf", html)
        self.assertIn("A4-Bogen", html)


if __name__ == "__main__":
    unittest.main()
