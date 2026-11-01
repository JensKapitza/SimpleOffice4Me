"""Capture batch adopt with library shelf handoff (#658c)."""
from __future__ import annotations

import io
import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.capture_inbox_store import CaptureInboxStore
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX
from tests.test_inventory_capture_batch import (
    BATCH_A,
    BATCH_B,
    ISBN_DEMO,
    PNG_RED_1PX,
)


class BatchShelfHandoffTests(unittest.TestCase):
    password = "capture-shelf-test-password"
    keys = ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in self.keys}
        self.documents = str(Path(self.temp.name) / "documents")
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=self.documents,
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            for username, admin in (("anna", 1), ("bob", 0)):
                db.execute(
                    "INSERT INTO user(username,password,display_name,is_admin,"
                    "is_disabled,auth_version,created_at,updated_at)"
                    " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, hash_password(self.password),
                     username.title(), admin),
                )
            db.commit()
        self.client = self.login("anna")
        self.bob = self.login("bob")
        self.token_counter = 0

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        client = app.test_client()
        page = client.get("/auth/login")
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": match.group(1).decode(),
            },
        )
        return client

    def csrf(self, client=None) -> str:
        page = (client or self.client).get("/inventory/capture")
        match = re.search(rb'data-csrf="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1).decode()

    def next_token(self) -> str:
        self.token_counter += 1
        return f"{self.token_counter:032x}"[-32:]

    def upload(self, data: bytes = PNG_1PX, batch_id: str = "",
               client=None):
        who = client or self.client
        form = {
            "photo": (io.BytesIO(data), "foto.png"),
            "client_request_id": self.next_token(),
            "_csrf_token": self.csrf(who),
        }
        if batch_id:
            form["batch_id"] = batch_id
        return who.post(
            "/inventory/capture/upload",
            data=form,
            content_type="multipart/form-data",
        )

    def adopt(self, batch_id: str, fields: dict, client=None):
        who = client or self.client
        form = dict(fields)
        form["_csrf_token"] = self.csrf(who)
        return who.post(
            f"/inventory/capture/batch/{batch_id}/adopt",
            data=form,
            content_type="multipart/form-data",
        )

    def make_shelf(self, name: str = "Regal A", code: str = "R1.A",
                   parent_id: str = "") -> dict:
        from app.library.store import LibraryStore

        return LibraryStore(self.documents).create_location(
            name, "anna", parent_id=parent_id, code=code)

    def assignment_of(self, object_id: str):
        from app.library.store import LibraryStore

        return LibraryStore(self.documents).assignment(object_id)

    def test_adopt_with_shelf_assigns_location(self) -> None:
        from app.object_store import ObjectStore

        shelf = self.make_shelf()
        self.upload(PNG_1PX, BATCH_A)
        self.upload(PNG_RED_1PX, BATCH_A)
        created = self.adopt(BATCH_A, {
            "title": "Regalbuch", "item_type": "book",
            "location_id": shelf["location_id"],
            "capture_request_id": self.next_token(),
        }).get_json()
        self.assertTrue(created["ok"])
        self.assertTrue(created["saved"])
        self.assertEqual(shelf["location_id"],
                         created["shelf"]["location_id"])
        self.assertEqual("R1.A", created["shelf"]["code"])
        self.assertIn("Regal A", created["shelf"]["path"])
        self.assertNotIn("shelf_error", created)
        self.assertTrue(any("R1.A" in note for note in created["notices"]))
        assignment = self.assignment_of(created["object_id"])
        assert assignment is not None
        self.assertEqual(shelf["location_id"], assignment["location_id"])
        with app.app_context():
            item = ObjectStore(self.documents).object(created["object_id"])
        self.assertEqual(shelf["location_id"],
                         item["fields"]["library_location_id"])
        self.assertEqual("R1.A", item["fields"]["library_location_code"])
        self.assertIn("Regal A", item["location"])

    def test_adopt_with_unknown_shelf_creates_nothing(self) -> None:
        from app.object_store import ObjectStore

        self.make_shelf()
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        with app.app_context():
            before = len(ObjectStore(self.documents).objects())
        for bad_id in ("0" * 32, "../fremd", "kein-regal"):
            response = self.adopt(BATCH_A, {
                "title": "Regalbuch", "item_type": "book",
                "location_id": bad_id,
                "capture_request_id": self.next_token(),
            })
            self.assertEqual(400, response.status_code)
            self.assertIn("Unbekannter Bibliotheksstandort",
                          response.get_json()["error"])
        with app.app_context():
            self.assertEqual(
                before, len(ObjectStore(self.documents).objects()))
        stored = CaptureInboxStore(self.documents).get(
            entry["entry_id"], "anna")
        assert stored is not None
        self.assertEqual("unassigned", stored["status"])

    def test_adopt_without_shelf_reports_clearly(self) -> None:
        self.upload(PNG_1PX, BATCH_A)
        created = self.adopt(BATCH_A, {
            "title": "Regalloses Buch", "item_type": "book",
            "capture_request_id": self.next_token(),
        }).get_json()
        self.assertTrue(created["ok"])
        self.assertTrue(created["saved"])
        self.assertIsNone(created["shelf"])
        self.assertNotIn("shelf_error", created)
        self.assertIn("Ohne Regal übernommen.", created["notices"])
        self.assertIsNone(self.assignment_of(created["object_id"]))

    def test_adopt_retry_same_token_assigns_once(self) -> None:
        from app.inventory import InventoryEnrichmentStore

        shelf = self.make_shelf()
        self.upload(PNG_1PX, BATCH_A)
        self.upload(PNG_RED_1PX, BATCH_A)
        token = self.next_token()
        first = self.adopt(BATCH_A, {
            "title": "Regalbuch", "item_type": "book",
            "location_id": shelf["location_id"],
            "capture_request_id": token,
        }).get_json()
        self.assertTrue(first["saved"])
        photos_before = len(InventoryEnrichmentStore(self.documents)
                            .object_meta(first["object_id"]).get("photos", []))
        second = self.adopt(BATCH_A, {
            "title": "Regalbuch", "item_type": "book",
            "location_id": shelf["location_id"],
            "capture_request_id": token,
        }).get_json()
        self.assertTrue(second["ok"])
        self.assertFalse(second["saved"])
        self.assertTrue(second["retried"])
        self.assertEqual(first["object_id"], second["object_id"])
        self.assertEqual(shelf["location_id"],
                         second["shelf"]["location_id"])
        self.assertNotIn("shelf_error", second)
        photos_after = len(InventoryEnrichmentStore(self.documents)
                           .object_meta(first["object_id"]).get("photos", []))
        self.assertEqual(photos_before, photos_after)
        self.assertEqual(2, photos_after)
        assignment = self.assignment_of(first["object_id"])
        assert assignment is not None
        self.assertEqual(shelf["location_id"], assignment["location_id"])

    def test_adopt_shelf_failure_then_retry(self) -> None:
        from unittest import mock

        from app.inventory_capture import SHELF_ASSIGN_ERROR

        shelf = self.make_shelf()
        self.upload(PNG_1PX, BATCH_A)
        token = self.next_token()
        with mock.patch(
            "app.library.routes.assign_object_to_location",
            side_effect=OSError("Regaldatei kaputt"),
        ):
            failed = self.adopt(BATCH_A, {
                "title": "Regalbuch", "item_type": "book",
                "location_id": shelf["location_id"],
                "capture_request_id": token,
            })
        self.assertEqual(200, failed.status_code)
        payload = failed.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["saved"])
        self.assertIsNone(payload["shelf"])
        self.assertEqual(SHELF_ASSIGN_ERROR, payload["shelf_error"])
        self.assertNotIn("kaputt", payload["shelf_error"])
        self.assertIsNone(self.assignment_of(payload["object_id"]))
        retry = self.adopt(BATCH_A, {
            "title": "Regalbuch", "item_type": "book",
            "location_id": shelf["location_id"],
            "capture_request_id": token,
        }).get_json()
        self.assertTrue(retry["ok"])
        self.assertFalse(retry["saved"])
        self.assertEqual(payload["object_id"], retry["object_id"])
        self.assertEqual(shelf["location_id"],
                         retry["shelf"]["location_id"])
        self.assertNotIn("shelf_error", retry)
        assignment = self.assignment_of(payload["object_id"])
        assert assignment is not None
        self.assertEqual(shelf["location_id"], assignment["location_id"])

    def test_adopt_duplicate_keeps_existing_shelf(self) -> None:
        from app.library.routes import assign_object_to_location
        from app.object_store import ObjectStore

        old_shelf = self.make_shelf("Altes Regal", "ALT.1")
        new_shelf = self.make_shelf("Neues Regal", "NEU.2")
        with app.app_context():
            existing = ObjectStore(self.documents).create(
                {"name": "Der Musterroman", "type": "book",
                 "identifier": ISBN_DEMO}, "anna")
        assign_object_to_location(
            existing["object_id"], old_shelf["location_id"], "anna",
            root=self.documents)
        self.upload(PNG_1PX, BATCH_A)
        payload = self.adopt(BATCH_A, {
            "title": "Der Musterroman", "item_type": "book",
            "isbn": ISBN_DEMO,
            "location_id": new_shelf["location_id"],
            "capture_request_id": self.next_token(),
        }).get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["duplicate"])
        self.assertEqual(existing["object_id"], payload["object_id"])
        self.assertEqual(old_shelf["location_id"],
                         payload["shelf"]["location_id"])
        self.assertTrue(any("nicht geändert" in note
                            for note in payload["notices"]))
        assignment = self.assignment_of(existing["object_id"])
        assert assignment is not None
        self.assertEqual(old_shelf["location_id"],
                         assignment["location_id"])

    def test_review_lists_shelves_with_path(self) -> None:
        parent = self.make_shelf("Wohnzimmer", "WOHN")
        child = self.make_shelf("Regal A", "WOHN.A",
                                parent_id=parent["location_id"])
        self.upload(PNG_1PX, BATCH_A)
        review = self.client.get(f"/inventory/capture/batch/{BATCH_A}")
        self.assertEqual(200, review.status_code)
        shelves = {row["location_id"]: row
                   for row in review.get_json()["shelves"]}
        self.assertEqual("WOHN", shelves[parent["location_id"]]["code"])
        self.assertEqual("WOHN.A", shelves[child["location_id"]]["code"])
        self.assertIn(" / ", shelves[child["location_id"]]["path"])

    def test_adopt_retry_empty_location_keeps_shelf(self) -> None:
        shelf = self.make_shelf()
        self.upload(PNG_1PX, BATCH_A)
        token = self.next_token()
        first = self.adopt(BATCH_A, {
            "title": "Regalbuch", "item_type": "book",
            "location_id": shelf["location_id"],
            "capture_request_id": token,
        }).get_json()
        self.assertTrue(first["saved"])
        second = self.adopt(BATCH_A, {
            "title": "Regalbuch", "item_type": "book",
            "capture_request_id": token,
        }).get_json()
        self.assertTrue(second["ok"])
        self.assertEqual(first["object_id"], second["object_id"])
        self.assertEqual(shelf["location_id"],
                         second["shelf"]["location_id"])
        self.assertTrue(any("bereits zugewiesen" in note
                            for note in second["notices"]))
        assignment = self.assignment_of(first["object_id"])
        assert assignment is not None
        self.assertEqual(shelf["location_id"], assignment["location_id"])


    def test_review_prefills_active_session_shelf(self) -> None:
        from app.library.scan_sessions import ScanSessionStore

        shelf = self.make_shelf()
        ScanSessionStore(self.documents).start(
            "anna", shelf["location_id"], "Regal A")
        self.upload(PNG_1PX, BATCH_A)
        review = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertEqual(shelf["location_id"],
                         review["default_location_id"])

    def test_review_prefills_paused_session_shelf(self) -> None:
        from app.library.scan_sessions import ScanSessionStore

        shelf = self.make_shelf()
        sessions = ScanSessionStore(self.documents)
        sessions.start("anna", shelf["location_id"], "Regal A")
        sessions.transition("anna", "pause")
        self.upload(PNG_1PX, BATCH_A)
        review = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertEqual(shelf["location_id"],
                         review["default_location_id"])

    def test_review_ignores_finished_session(self) -> None:
        from app.library.scan_sessions import ScanSessionStore

        shelf = self.make_shelf()
        sessions = ScanSessionStore(self.documents)
        sessions.start("anna", shelf["location_id"], "Regal A")
        sessions.transition("anna", "finish")
        self.upload(PNG_1PX, BATCH_A)
        review = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertEqual("", review["default_location_id"])

    def test_review_never_leaks_foreign_session(self) -> None:
        from app.library.scan_sessions import ScanSessionStore

        shelf_a = self.make_shelf("Regal A", "A.1")
        shelf_b = self.make_shelf("Regal B", "B.2")
        sessions = ScanSessionStore(self.documents)
        sessions.start("bob", shelf_b["location_id"], "Regal B")
        self.upload(PNG_1PX, BATCH_A)
        anna = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertEqual("", anna["default_location_id"])
        sessions.start("anna", shelf_a["location_id"], "Regal A")
        self.upload(PNG_RED_1PX, BATCH_B, client=self.bob)
        bob = self.bob.get(
            f"/inventory/capture/batch/{BATCH_B}").get_json()
        self.assertEqual(shelf_b["location_id"],
                         bob["default_location_id"])

    def test_review_ignores_unknown_session_location(self) -> None:
        from app.library.scan_sessions import ScanSessionStore

        ScanSessionStore(self.documents).start(
            "anna", "0" * 32, "Phantomregal")
        self.upload(PNG_1PX, BATCH_A)
        review = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertEqual("", review["default_location_id"])

    def test_review_without_session_defaults_empty(self) -> None:
        self.upload(PNG_1PX, BATCH_A)
        review = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertEqual("", review["default_location_id"])


if __name__ == "__main__":
    unittest.main()
