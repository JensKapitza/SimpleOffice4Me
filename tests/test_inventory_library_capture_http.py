"""HTTP contracts for capturing a scanned book into its selected library shelf."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.library.store import LibraryStore
from app.object_store import ObjectStore


class InventoryLibraryCaptureHttpContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.document_root = root / "documents"
        keys = ("TESTING", "DATABASE", "DOCUMENT_ROOT", "TEST_CSRF_PROTECTION", "SECRET_KEY")
        saved = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(self.document_root),
            TEST_CSRF_PROTECTION=True,
            SECRET_KEY="inventory-capture-test-secret",
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('capture-member','unused',0)")
            db.commit()
            self.user_id = db.execute("SELECT id FROM user WHERE username='capture-member'").fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = self.user_id
            state["_csrf_token"] = "inventory-capture-csrf-token-long-enough"
        self.location = LibraryStore(self.document_root).create_location("Regal A", "capture-member")
        self.capture_token = "a" * 32

    def capture_url(self):
        return (
            "/inventory?barcode=9780131103627&book=1&library_return=1"
            f"&library_location={self.location['location_id']}"
        )

    def capture_form(self, **overrides):
        values = {
            "_csrf_token": "inventory-capture-csrf-token-long-enough",
            "library_return": "1",
            "capture_request_id": self.capture_token,
            "library_location_id": self.location["location_id"],
            "item_type": "book",
            "title": "Offline Handbuch",
            "barcode": "9780131103627",
            "isbn": "9780131103627",
        }
        values.update(overrides)
        return values

    def book_items(self):
        return [item for item in ObjectStore(self.document_root).objects() if item.get("type") == "book"]

    def test_capture_form_prefills_scanned_code_and_exposes_editable_shelf(self):
        response = self.client.get(self.capture_url())

        self.assertEqual(200, response.status_code)
        self.assertIn(b'name="barcode" value="9780131103627"', response.data)
        self.assertIn(b'name="isbn" value="9780131103627"', response.data)
        self.assertIn(b'name="library_location_id"', response.data)
        self.assertIn(self.location["location_id"].encode(), response.data)
        self.assertIn(b"Titel manuell eingeben oder Buchdaten laden", response.data)

    def test_manual_offline_capture_assigns_book_and_shows_direct_inventory_link(self):
        response = self.client.post("/inventory/items", data=self.capture_form())

        self.assertEqual(302, response.status_code)
        self.assertIn("/library?", response.location)
        item = self.book_items()[0]
        assignment = LibraryStore(self.document_root).assignment(item["object_id"])
        self.assertEqual(self.location["location_id"], assignment["location_id"])
        self.assertEqual("Offline Handbuch", item["name"])
        self.assertEqual("book", item["type"])

        listing = self.client.get(response.location)
        self.assertEqual(200, listing.status_code)
        self.assertIn(b"Offline Handbuch", listing.data)
        self.assertIn("Inventareintrag öffnen".encode("utf-8"), listing.data)
        self.assertIn(f"/inventory/{item['object_id']}".encode(), listing.data)

    def test_capture_uses_the_shelf_selected_in_the_editable_assignment_field(self):
        other = LibraryStore(self.document_root).create_location("Regal B", "capture-member")

        response = self.client.post(
            "/inventory/items",
            data=self.capture_form(library_location_id=other["location_id"]),
        )

        item = self.book_items()[0]
        assignment = LibraryStore(self.document_root).assignment(item["object_id"])
        self.assertEqual(302, response.status_code)
        self.assertEqual(other["location_id"], assignment["location_id"])
        self.assertIn(f"location={other['location_id']}", response.location)

    def test_same_capture_request_is_idempotent_even_when_duplicate_option_is_checked(self):
        form = self.capture_form(allow_duplicate="1")
        first = self.client.post("/inventory/items", data=form)
        second = self.client.post("/inventory/items", data=form)

        self.assertEqual(302, first.status_code)
        self.assertEqual(first.location, second.location)
        self.assertEqual(1, len(self.book_items()))
        self.assertEqual(1, len(LibraryStore(self.document_root).contents(self.location["location_id"])))

    def test_retry_completes_a_saved_book_after_the_first_shelf_assignment_fails(self):
        form = self.capture_form()
        with patch("app.library.routes.assign_object_to_location", side_effect=OSError("shelf store unavailable")):
            failed_assignment = self.client.post("/inventory/items", data=form)

        self.assertEqual(302, failed_assignment.status_code)
        item = self.book_items()[0]
        self.assertIsNone(LibraryStore(self.document_root).assignment(item["object_id"]))

        retry = self.client.post("/inventory/items", data=form)

        self.assertEqual(302, retry.status_code)
        self.assertIn("/library?", retry.location)
        self.assertEqual(1, len(self.book_items()))
        assignment = LibraryStore(self.document_root).assignment(item["object_id"])
        self.assertEqual(self.location["location_id"], assignment["location_id"])

    def test_missing_title_does_not_create_an_unnamed_book_and_keeps_capture_context(self):
        response = self.client.post("/inventory/items", data=self.capture_form(title=""))

        self.assertEqual(302, response.status_code)
        self.assertIn("library_return=1", response.location)
        self.assertEqual([], self.book_items())
        page = self.client.get(response.location)
        self.assertEqual(200, page.status_code)
        self.assertIn("Bitte vor der Übernahme einen Buchtitel eingeben".encode("utf-8"), page.data)
        self.assertIn(b'name="barcode" value="9780131103627"', page.data)

    def test_invalid_shelf_is_rejected_before_object_creation(self):
        response = self.client.post(
            "/inventory/items",
            data=self.capture_form(library_location_id="missing-location"),
        )

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.startswith("/inventory"))
        self.assertEqual([], self.book_items())


if __name__ == "__main__":
    unittest.main()
