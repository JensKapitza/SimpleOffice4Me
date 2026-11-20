"""Blackbox: virtual collections without moving objects (Issue #1111, P2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore
from app.object_store import ObjectStore


class CollectionHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "curator", "password": "curator-password-123"})
        self.client.post("/auth/login", data={"username": "curator", "password": "curator-password-123"})
        self.root = base / "documents"

    def tearDown(self):
        app.config.update(self.saved)

    def create_item(self, title):
        response = self.client.post("/inventory/items", data={
            "response_format": "json", "title": title, "item_type": "object",
        })
        self.assertEqual(200, response.status_code)
        return response.get_json()["object_id"]

    def test_collection_groups_objects_by_theme(self):
        created = self.client.post("/inventory/collections", json={"name": "Werkstatt"})
        self.assertEqual(201, created.status_code)
        collection_id = created.get_json()["collection"]["collection_id"]
        hammer = self.create_item("Hammer")
        saw = self.create_item("Säge")
        for object_id in (hammer, saw):
            added = self.client.post("/inventory/collections/members", json={
                "collection_id": collection_id, "object_id": object_id})
            self.assertEqual(201, added.status_code)
        listing = self.client.get("/inventory/collections").get_json()
        self.assertEqual(1, len(listing["collections"]))
        self.assertEqual(2, listing["collections"][0]["member_count"])
        detail = self.client.get(f"/inventory/{hammer}").get_data(as_text=True)
        self.assertIn("Werkstatt", detail)

    def test_collection_never_changes_storage_location(self):
        store = LibraryStore(self.root)
        box = store.create_location("Kiste", "curator")
        shelf = store.create_location("Regal", "curator")
        hammer = self.create_item("Hammer")
        store.assign(hammer, box["location_id"], "curator")
        created = self.client.post("/inventory/collections", json={"name": "Thema"})
        collection_id = created.get_json()["collection"]["collection_id"]
        self.client.post("/inventory/collections/members", json={
            "collection_id": collection_id, "object_id": hammer})
        other = self.create_item("Säge")
        store.assign(other, shelf["location_id"], "counter")
        self.client.post("/inventory/collections/members", json={
            "collection_id": collection_id, "object_id": other})
        self.assertEqual(box["location_id"],
                         store.assignment(hammer)["location_id"])
        self.assertEqual(shelf["location_id"],
                         store.assignment(other)["location_id"])

    def test_members_can_be_removed(self):
        created = self.client.post("/inventory/collections", json={"name": "Werkstatt"})
        collection_id = created.get_json()["collection"]["collection_id"]
        hammer = self.create_item("Hammer")
        self.client.post("/inventory/collections/members", json={
            "collection_id": collection_id, "object_id": hammer})
        removed = self.client.post(
            f"/inventory/collections/{collection_id}/members/remove",
            json={"object_id": hammer})
        self.assertEqual(200, removed.status_code)
        self.assertEqual([], removed.get_json()["collection"]["members"])
        again = self.client.post(
            f"/inventory/collections/{collection_id}/members/remove",
            json={"object_id": hammer})
        self.assertEqual(400, again.status_code)

    def test_collections_are_validated(self):
        empty_name = self.client.post("/inventory/collections", json={"name": "  "})
        self.assertEqual(400, empty_name.status_code)
        first = self.client.post("/inventory/collections", json={"name": "Werkstatt"})
        self.assertEqual(201, first.status_code)
        duplicate = self.client.post("/inventory/collections", json={"name": "werkstatt"})
        self.assertEqual(400, duplicate.status_code)
        collection_id = first.get_json()["collection"]["collection_id"]
        unknown_object = self.client.post("/inventory/collections/members", json={
            "collection_id": collection_id, "object_id": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(400, unknown_object.status_code)
        hammer = self.create_item("Hammer")
        unknown_collection = self.client.post("/inventory/collections/members", json={
            "collection_id": "00000000-0000-0000-0000-000000000000", "object_id": hammer})
        self.assertEqual(400, unknown_collection.status_code)

    def test_collections_require_login_and_ui_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post("/inventory/collections",
                                json={"name": "x"}, follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        index_html = self.client.get("/inventory").get_data(as_text=True)
        self.assertIn("data-collections", index_html)
        self.assertIn("Sammlungen", index_html)
        hammer = self.create_item("Hammer")
        detail_html = self.client.get(f"/inventory/{hammer}").get_data(as_text=True)
        self.assertIn("data-containing-collections", detail_html)


if __name__ == "__main__":
    unittest.main()
