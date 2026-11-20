"""Blackbox: storage locations move with their whole subtree (Issue #1111, P1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.library.store import LibraryStore
from app.object_store import ObjectStore


class LibraryLocationMoveHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "mover", "password": "mover-password-123"})
        self.client.post("/auth/login", data={"username": "mover", "password": "mover-password-123"})
        self.root = base / "documents"
        store = LibraryStore(self.root)
        self.house = store.create_location("Haus", "mover")
        self.room = store.create_location("Raum", "mover", parent_id=self.house["location_id"])
        self.box = store.create_location("Kiste", "mover", parent_id=self.room["location_id"])
        self.inner = store.create_location("Innenfach", "mover", parent_id=self.box["location_id"])
        self.other_room = store.create_location("Anderer Raum", "mover", parent_id=self.house["location_id"])
        objects = ObjectStore(self.root)
        self.book_in_box = objects.create(
            {"name": "Box Book", "type": "book", "identifier": "9783000000001"}, "mover")
        self.book_in_inner = objects.create(
            {"name": "Inner Book", "type": "book", "identifier": "9783000000002"}, "mover")
        store.assign(self.book_in_box["object_id"], self.box["location_id"], "mover")
        store.assign(self.book_in_inner["object_id"], self.inner["location_id"], "mover")

    def tearDown(self):
        app.config.update(self.saved)

    def book_path(self, identifier):
        response = self.client.get("/library/find", query_string={"q": identifier})
        self.assertEqual(200, response.status_code)
        return response.get_json()["location_path"]

    def test_move_box_refreshes_recursive_paths(self):
        self.assertEqual("Haus / Raum / Kiste", self.book_path("9783000000001"))
        response = self.client.post(
            f"/library/locations/{self.box['location_id']}/move",
            json={"parent_id": self.other_room["code"]},
        )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual("Haus / Anderer Raum / Kiste", body["path"])
        self.assertEqual([], body["failed"])
        self.assertIn(self.book_in_box["object_id"], body["remirrored"])
        self.assertIn(self.book_in_inner["object_id"], body["remirrored"])
        self.assertEqual("Haus / Anderer Raum / Kiste", self.book_path("9783000000001"))
        self.assertEqual("Haus / Anderer Raum / Kiste / Innenfach", self.book_path("9783000000002"))
        resolve = self.client.get(
            "/library/locations/resolve", query_string={"code": self.inner["code"]})
        self.assertEqual("Haus / Anderer Raum / Kiste / Innenfach", resolve.get_json()["path"])

    def test_move_into_own_descendant_is_rejected(self):
        response = self.client.post(
            f"/library/locations/{self.room['location_id']}/move",
            json={"parent_id": self.inner["location_id"]},
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("eigenen Unterstandort", response.get_json()["error"])
        self.assertEqual("Haus / Raum / Kiste", self.book_path("9783000000001"))
        self.assertEqual("Haus / Raum / Kiste / Innenfach", self.book_path("9783000000002"))

    def test_move_validates_target_location_and_login(self):
        unknown_target = self.client.post(
            f"/library/locations/{self.box['location_id']}/move",
            json={"parent_id": "LIB-L9999"},
        )
        self.assertEqual(400, unknown_target.status_code)
        unknown_location = self.client.post(
            "/library/locations/00000000-0000-0000-0000-000000000000/move",
            json={"parent_id": self.room["location_id"]},
        )
        self.assertEqual(404, unknown_location.status_code)
        anonymous = app.test_client()
        denied = anonymous.post(
            f"/library/locations/{self.box['location_id']}/move",
            json={"parent_id": self.room["location_id"]}, follow_redirects=False,
        )
        self.assertIn(denied.status_code, (302, 303))

    def test_move_to_top_level_and_self_move(self):
        top = self.client.post(
            f"/library/locations/{self.box['location_id']}/move", json={"parent_id": ""})
        self.assertEqual(200, top.status_code)
        self.assertEqual("Kiste", top.get_json()["path"])
        self.assertEqual("Kiste / Innenfach", self.book_path("9783000000002"))
        itself = self.client.post(
            f"/library/locations/{self.box['location_id']}/move",
            json={"parent_id": self.box["location_id"]},
        )
        self.assertEqual(400, itself.status_code)
        self.assertIn("in sich selbst", itself.get_json()["error"])

    def test_move_form_is_visible_on_library_page(self):
        response = self.client.get("/library")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("data-library-move", html)
        self.assertIn(f"/library/locations/{self.box['location_id']}/move", html)
        self.assertIn(self.other_room["code"], html)


if __name__ == "__main__":
    unittest.main()
