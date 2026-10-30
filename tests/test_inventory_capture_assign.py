"""Capture batch assign to existing book + photo roles (#658)."""
from __future__ import annotations

import io
import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.capture_inbox_store import CaptureInboxStore
from app.db import ensure_auth_database, get_db
from app.document_store import CONTROL_DIR
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX
from tests.test_inventory_capture_batch import (
    BATCH_A,
    BATCH_B,
    ISBN_DEMO,
    PNG_BLUE_1PX,
    PNG_RED_1PX,
)


class BatchAssignRouteTests(unittest.TestCase):
    password = "capture-batch-test-password"
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
               client=None, request_id: str | None = None):
        who = client or self.client
        form = {
            "photo": (io.BytesIO(data), "foto.png"),
            "client_request_id": (request_id if request_id is not None
                                  else self.next_token()),
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

    def assign_group(self, batch_id: str, object_id: str, client=None):
        who = client or self.client
        return who.post(
            f"/inventory/capture/batch/{batch_id}/assign",
            data={"object_id": object_id, "_csrf_token": self.csrf(who)},
            content_type="multipart/form-data",
        )

    def set_role(self, entry_id: str, role: str, client=None):
        who = client or self.client
        return who.post(
            f"/inventory/capture/{entry_id}/role",
            data={"role": role, "_csrf_token": self.csrf(who)},
            content_type="multipart/form-data",
        )

    def _book_target(self, name="Der Musterroman", type_="book"):
        from app.object_store import ObjectStore

        with app.app_context():
            return ObjectStore(self.documents).create(
                {"name": name, "type": type_}, "anna")

    def _photos_of(self, object_id: str) -> list:
        from app.inventory import InventoryEnrichmentStore

        return InventoryEnrichmentStore(self.documents).object_meta(
            object_id).get("photos", [])

    def test_batch_assign_links_group_to_existing_book(self) -> None:
        from app.object_store import ObjectStore

        entries = [
            self.upload(data, BATCH_A).get_json()["entry"]
            for data in (PNG_1PX, PNG_RED_1PX, PNG_BLUE_1PX)
        ]
        roles = ["title", "imprint", "spine"]
        for entry, role in zip(entries, roles):
            response = self.set_role(entry["entry_id"], role)
            self.assertEqual(200, response.status_code)
            self.assertEqual(role, response.get_json()["entry"]["photo_role"])
        book = self._book_target()
        with app.app_context():
            display = ObjectStore(self.documents).object(
                book["object_id"])["display_id"]
            before = len(ObjectStore(self.documents).objects())
        review = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertIn("assign_url", review)
        assigned = self.assign_group(BATCH_A, f"#{display}").get_json()
        self.assertTrue(assigned["ok"])
        self.assertEqual(book["object_id"], assigned["object_id"])
        self.assertEqual(3, assigned["assigned"])
        self.assertEqual(3, assigned["photo_count"])
        self.assertEqual(3, assigned["attached"])
        self.assertEqual([], assigned["notices"])
        with app.app_context():
            self.assertEqual(before, len(ObjectStore(self.documents).objects()))
        store = CaptureInboxStore(self.documents)
        for entry in entries:
            stored = store.get(entry["entry_id"], "anna")
            assert stored is not None
            self.assertEqual("assigned", stored["status"])
            self.assertEqual(book["object_id"],
                             stored["assignment"]["object_id"])
        photos = self._photos_of(book["object_id"])
        self.assertEqual(3, len(photos))
        self.assertEqual(sorted(roles), sorted(
            photo.get("role", "") for photo in photos))
        for entry, data in zip(entries, (PNG_1PX, PNG_RED_1PX, PNG_BLUE_1PX)):
            self.assertEqual(
                data, store.media_path(entry["entry_id"], "anna").read_bytes())

    def test_batch_assign_rejects_non_book_target(self) -> None:
        hammer = self._book_target("Hammer", "object")
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        response = self.assign_group(BATCH_A, hammer["object_id"])
        self.assertEqual(400, response.status_code)
        self.assertIn("Buch erwartet", response.get_json()["error"])
        stored = CaptureInboxStore(self.documents).get(entry["entry_id"], "anna")
        assert stored is not None
        self.assertEqual("unassigned", stored["status"])
        self.assertEqual([], self._photos_of(hammer["object_id"]))

    def test_batch_assign_unknown_target_and_group(self) -> None:
        book = self._book_target()
        self.upload(PNG_1PX, BATCH_A)
        missing = self.assign_group(BATCH_A, "0" * 32)
        self.assertEqual(400, missing.status_code)
        self.assertIn("nicht verfügbar", missing.get_json()["error"])
        empty = self.assign_group("cc" * 16, book["object_id"])
        self.assertEqual(404, empty.status_code)
        malformed = self.assign_group("not-a-batch", book["object_id"])
        self.assertEqual(400, malformed.status_code)

    def test_batch_assign_foreign_actor_sees_nothing(self) -> None:
        book = self._book_target()
        foreign = self.upload(PNG_1PX, BATCH_B, client=self.bob).get_json()["entry"]
        response = self.assign_group(BATCH_B, book["object_id"])
        self.assertEqual(404, response.status_code)
        stored = CaptureInboxStore(self.documents).get(foreign["entry_id"], "bob")
        assert stored is not None
        self.assertEqual("unassigned", stored["status"])
        self.assertEqual([], self._photos_of(book["object_id"]))

    def test_batch_assign_retry_is_idempotent(self) -> None:
        book = self._book_target()
        self.upload(PNG_1PX, BATCH_A)
        self.upload(PNG_RED_1PX, BATCH_A)
        first = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(first["ok"])
        self.assertEqual(2, first["attached"])
        second = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(second["ok"])
        self.assertEqual(2, second["assigned"])
        self.assertEqual(0, second["attached"])
        self.assertEqual([], second["notices"])
        self.assertEqual(2, len(self._photos_of(book["object_id"])))

    def test_batch_assign_conflict_aborts_whole_call(self) -> None:
        book = self._book_target()
        other = self._book_target("Anderes Buch")
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        moved = self.client.post(
            f"/inventory/capture/{first['entry_id']}/assign",
            data={"kind": "book", "object_id": other["object_id"],
                  "_csrf_token": self.csrf()},
            content_type="multipart/form-data",
        )
        self.assertEqual(200, moved.status_code)
        response = self.assign_group(BATCH_A, book["object_id"])
        self.assertEqual(400, response.status_code)
        self.assertIn(first["entry_id"][:8], response.get_json()["error"])
        store = CaptureInboxStore(self.documents)
        kept = store.get(second["entry_id"], "anna")
        assert kept is not None
        self.assertEqual("unassigned", kept["status"])
        self.assertEqual([], self._photos_of(book["object_id"]))

    def test_batch_assign_partial_failure_reports_and_stays_retryable(self) -> None:
        book = self._book_target()
        broken = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        healthy = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        store = CaptureInboxStore(self.documents)
        store.media_path(broken["entry_id"], "anna").unlink()
        payload = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(2, payload["assigned"])
        self.assertEqual(1, payload["attached"])
        self.assertEqual(1, len(payload["notices"]))
        self.assertIn(broken["entry_id"][:8], payload["notices"][0])
        self.assertEqual(1, len(self._photos_of(book["object_id"])))
        retry = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(retry["ok"])
        self.assertEqual(1, len(self._photos_of(book["object_id"])))

    def test_photo_role_set_correct_clear_and_history(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        store = CaptureInboxStore(self.documents)

        def role_events():
            stored = store.get(entry["entry_id"], "anna")
            assert stored is not None
            return [event for event in stored.get("history", [])
                    if event.get("event") == "photo_role"]

        first = self.set_role(entry["entry_id"], "title")
        self.assertEqual(200, first.status_code)
        self.assertEqual("title", first.get_json()["entry"]["photo_role"])
        self.assertEqual(1, len(role_events()))
        repeat = self.set_role(entry["entry_id"], "title")
        self.assertEqual(200, repeat.status_code)
        self.assertEqual(1, len(role_events()))
        corrected = self.set_role(entry["entry_id"], "spine")
        self.assertEqual("spine", corrected.get_json()["entry"]["photo_role"])
        events = role_events()
        self.assertEqual(2, len(events))
        self.assertEqual("title", events[-1]["previous_role"])
        cleared = self.set_role(entry["entry_id"], "")
        self.assertEqual("", cleared.get_json()["entry"]["photo_role"])
        self.assertEqual(3, len(role_events()))

    def test_photo_role_invalid_and_foreign(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        bad = self.set_role(entry["entry_id"], "cover")
        self.assertEqual(400, bad.status_code)
        self.assertIn("ungültig", bad.get_json()["error"])
        stored = CaptureInboxStore(self.documents).get(entry["entry_id"], "anna")
        assert stored is not None
        self.assertEqual("", stored.get("photo_role", ""))
        self.assertEqual(
            404, self.set_role(entry["entry_id"], "title", client=self.bob).status_code)
        self.assertEqual(
            404, self.set_role("f" * 32, "title").status_code)

    def test_roles_preserved_on_adopt_and_assign(self) -> None:
        from app.inventory import InventoryEnrichmentStore

        self.upload(PNG_1PX, BATCH_A)
        red = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        self.set_role(red["entry_id"], "imprint")
        created = self.adopt(BATCH_A, {
            "title": "Rollenbuch", "isbn": ISBN_DEMO, "item_type": "book",
            "capture_request_id": self.next_token(),
        }).get_json()
        self.assertTrue(created["saved"])
        adopted = InventoryEnrichmentStore(self.documents).object_meta(
            created["object_id"]).get("photos", [])
        self.assertEqual({"", "imprint"},
                         {photo.get("role", "") for photo in adopted})
        book = self._book_target("Zielbuch")
        self.upload(PNG_BLUE_1PX, BATCH_B)
        green = self.upload(PNG_1PX, BATCH_B).get_json()["entry"]
        self.set_role(green["entry_id"], "spine")
        linked = self.assign_group(BATCH_B, book["object_id"]).get_json()
        self.assertTrue(linked["ok"])
        attached = InventoryEnrichmentStore(self.documents).object_meta(
            book["object_id"]).get("photos", [])
        self.assertEqual({"", "spine"},
                         {photo.get("role", "") for photo in attached})

    def test_assign_many_conflict_writes_nothing(self) -> None:
        book = self._book_target()
        other = self._book_target("Anderes Buch")
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        store = CaptureInboxStore(self.documents)
        store.assign(first["entry_id"], "anna", "book", other["object_id"])
        before = Path(store.index_path).read_bytes()
        result = store.assign_many(
            [first["entry_id"], second["entry_id"]],
            "anna", "book", book["object_id"])
        self.assertIsNone(result)
        self.assertEqual(before, Path(store.index_path).read_bytes())
        kept = store.get(second["entry_id"], "anna")
        assert kept is not None
        self.assertEqual("unassigned", kept["status"])
        self.assertEqual({}, kept.get("assignment", {}))

    def test_assign_many_rejects_mixed_foreign_unknown_atomically(self) -> None:
        book = self._book_target()
        mine = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        foreign = self.upload(
            PNG_RED_1PX, BATCH_B, client=self.bob).get_json()["entry"]
        store = CaptureInboxStore(self.documents)
        before = Path(store.index_path).read_bytes()
        self.assertIsNone(store.assign_many(
            [mine["entry_id"], foreign["entry_id"]],
            "anna", "book", book["object_id"]))
        self.assertIsNone(store.assign_many(
            [mine["entry_id"], "0" * 32], "anna", "book", book["object_id"]))
        self.assertIsNone(store.assign_many(
            [mine["entry_id"], "not-an-id"], "anna", "book", book["object_id"]))
        self.assertEqual(before, Path(store.index_path).read_bytes())
        kept = store.get(mine["entry_id"], "anna")
        assert kept is not None
        self.assertEqual("unassigned", kept["status"])

    def test_assign_many_idempotent_without_new_history(self) -> None:
        book = self._book_target()
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        store = CaptureInboxStore(self.documents)
        ids = [first["entry_id"], second["entry_id"]]
        linked = store.assign_many(ids, "anna", "book", book["object_id"])
        assert linked is not None
        self.assertEqual(2, len(linked))
        self.assertIsNotNone(store.assign_many(
            ids, "anna", "book", book["object_id"]))
        self.assertIsNotNone(store.assign_many(
            [ids[0], ids[0]], "anna", "book", book["object_id"]))
        for entry_id in ids:
            stored = store.get(entry_id, "anna")
            assert stored is not None
            assigned = [event for event in stored.get("history", [])
                        if event.get("event") == "assigned"]
            self.assertEqual(1, len(assigned))

    def test_assign_many_validates_arguments(self) -> None:
        store = CaptureInboxStore(self.documents)
        with self.assertRaises(ValueError):
            store.assign_many([], "anna", "book", "0" * 32)
        with self.assertRaises(ValueError):
            store.assign_many(["0" * 32], "anna", "shelf", "0" * 32)
        with self.assertRaises(ValueError):
            store.assign_many(
                ["0" * 32], "anna", "book", "0" * 32,
                expected_batch_id="not-a-batch")

    def test_assign_many_rejects_regrouped_batch(self) -> None:
        book = self._book_target()
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        store = CaptureInboxStore(self.documents)
        moved = store.set_batch(second["entry_id"], "anna", BATCH_B)
        assert moved is not None
        before = Path(store.index_path).read_bytes()
        result = store.assign_many(
            [first["entry_id"], second["entry_id"]],
            "anna", "book", book["object_id"], expected_batch_id=BATCH_A)
        self.assertIsNone(result)
        self.assertEqual(before, Path(store.index_path).read_bytes())
        for entry in (first, second):
            stored = store.get(entry["entry_id"], "anna")
            assert stored is not None
            self.assertEqual("unassigned", stored["status"])
        kept = store.assign_many(
            [first["entry_id"]], "anna", "book", book["object_id"],
            expected_batch_id=BATCH_A)
        self.assertIsNotNone(kept)

    def test_batch_assign_race_aborts_without_links(self) -> None:
        from unittest import mock

        book = self._book_target()
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        with mock.patch(
            "app.capture_inbox_store.CaptureInboxStore.assign_many",
            return_value=None,
        ):
            response = self.assign_group(BATCH_A, book["object_id"])
        self.assertEqual(400, response.status_code)
        self.assertIn("zwischenzeitlich", response.get_json()["error"])
        store = CaptureInboxStore(self.documents)
        for entry in (first, second):
            stored = store.get(entry["entry_id"], "anna")
            assert stored is not None
            self.assertEqual("unassigned", stored["status"])
        self.assertEqual([], self._photos_of(book["object_id"]))

    def test_role_correction_updates_attached_photo(self) -> None:
        import hashlib

        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.upload(PNG_RED_1PX, BATCH_A)
        self.set_role(first["entry_id"], "title")
        book = self._book_target()
        linked = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(linked["ok"])
        changed = self.set_role(first["entry_id"], "spine")
        self.assertEqual(200, changed.status_code)
        payload = changed.get_json()
        self.assertEqual("spine", payload["entry"]["photo_role"])
        self.assertTrue(payload["attached_role_updated"])
        photos = {photo["sha256"]: photo
                  for photo in self._photos_of(book["object_id"])}
        digest = hashlib.sha256(PNG_1PX).hexdigest()
        self.assertEqual("spine", photos[digest].get("role"))
        other = hashlib.sha256(PNG_RED_1PX).hexdigest()
        self.assertNotIn("role", photos[other])

    def test_role_correction_repeat_writes_nothing(self) -> None:
        from app.inventory import InventoryEnrichmentStore

        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.set_role(entry["entry_id"], "title")
        book = self._book_target()
        self.assign_group(BATCH_A, book["object_id"])
        changed = self.set_role(entry["entry_id"], "spine").get_json()
        self.assertTrue(changed["attached_role_updated"])
        sidecar = InventoryEnrichmentStore(self.documents)
        before = Path(sidecar.index_path).read_bytes()
        repeat = self.set_role(entry["entry_id"], "spine").get_json()
        self.assertFalse(repeat["attached_role_updated"])
        self.assertEqual(before, Path(sidecar.index_path).read_bytes())

    def test_role_clear_removes_attached_role(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.set_role(entry["entry_id"], "title")
        book = self._book_target()
        self.assign_group(BATCH_A, book["object_id"])
        cleared = self.set_role(entry["entry_id"], "").get_json()
        self.assertEqual("", cleared["entry"]["photo_role"])
        self.assertTrue(cleared["attached_role_updated"])
        photos = self._photos_of(book["object_id"])
        self.assertEqual(1, len(photos))
        self.assertNotIn("role", photos[0])

    def test_role_unassigned_reports_no_attached_update(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        response = self.set_role(entry["entry_id"], "title")
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.get_json()["attached_role_updated"])

    def test_role_foreign_actor_leaves_attached_photo(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.set_role(entry["entry_id"], "title")
        book = self._book_target()
        self.assign_group(BATCH_A, book["object_id"])
        denied = self.set_role(entry["entry_id"], "spine", client=self.bob)
        self.assertEqual(404, denied.status_code)
        photos = self._photos_of(book["object_id"])
        self.assertEqual("title", photos[0].get("role"))

    def test_role_unknown_leaves_attached_photo(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.set_role(entry["entry_id"], "title")
        book = self._book_target()
        self.assign_group(BATCH_A, book["object_id"])
        bad = self.set_role(entry["entry_id"], "cover")
        self.assertEqual(400, bad.status_code)
        stored = CaptureInboxStore(self.documents).get(entry["entry_id"], "anna")
        assert stored is not None
        self.assertEqual("title", stored.get("photo_role"))
        photos = self._photos_of(book["object_id"])
        self.assertEqual("title", photos[0].get("role"))

    def test_photo_role_label_fail_closed(self) -> None:
        from app.capture_inbox_store import photo_role_label

        self.assertEqual("Titelblatt", photo_role_label("title"))
        self.assertEqual("Impressum", photo_role_label("imprint"))
        self.assertEqual("Buchrücken", photo_role_label("spine"))
        self.assertEqual("Sonstiges", photo_role_label("other"))
        self.assertEqual("", photo_role_label(""))
        self.assertEqual("", photo_role_label("cover"))
        self.assertEqual("", photo_role_label(None))

    def test_item_detail_shows_photo_role_badge(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.set_role(entry["entry_id"], "title")
        book = self._book_target()
        linked = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(linked["ok"])
        page = self.client.get(linked["detail_url"])
        self.assertEqual(200, page.status_code)
        html = page.data.decode("utf-8")
        self.assertIn("Titelblatt", html)
        self.assertIn('alt="Inventarfoto – Titelblatt"', html)

    def test_item_detail_hides_badge_without_role(self) -> None:
        self.upload(PNG_1PX, BATCH_A)
        book = self._book_target()
        linked = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(linked["ok"])
        page = self.client.get(linked["detail_url"])
        self.assertEqual(200, page.status_code)
        html = page.data.decode("utf-8")
        self.assertIn('alt="Inventarfoto"', html)

    def test_role_sync_failure_warns_and_stays_retryable(self) -> None:
        from unittest import mock

        from app.inventory_capture import ATTACHED_ROLE_SYNC_ERROR

        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.set_role(entry["entry_id"], "title")
        book = self._book_target()
        self.assign_group(BATCH_A, book["object_id"])
        with mock.patch(
            "app.inventory.InventoryEnrichmentStore.set_photo_role_by_sha",
            side_effect=OSError("disk voll"),
        ):
            failed = self.set_role(entry["entry_id"], "spine")
        self.assertEqual(200, failed.status_code)
        payload = failed.get_json()
        self.assertEqual("spine", payload["entry"]["photo_role"])
        self.assertFalse(payload["attached_role_updated"])
        self.assertEqual(ATTACHED_ROLE_SYNC_ERROR,
                         payload.get("attached_role_error"))
        self.assertNotIn("disk voll", payload["attached_role_error"])
        stored = CaptureInboxStore(self.documents).get(entry["entry_id"], "anna")
        assert stored is not None
        self.assertEqual("spine", stored.get("photo_role"))
        photos = self._photos_of(book["object_id"])
        self.assertEqual("title", photos[0].get("role"))
        retry = self.set_role(entry["entry_id"], "spine").get_json()
        self.assertTrue(retry["attached_role_updated"])
        self.assertNotIn("attached_role_error", retry)
        photos = self._photos_of(book["object_id"])
        self.assertEqual("spine", photos[0].get("role"))

    def test_object_care_shows_photo_role_badge(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.set_role(entry["entry_id"], "imprint")
        book = self._book_target()
        linked = self.assign_group(BATCH_A, book["object_id"]).get_json()
        self.assertTrue(linked["ok"])
        page = self.client.get(f"/library/objects/{book['object_id']}/care")
        self.assertEqual(200, page.status_code)
        html = page.data.decode("utf-8")
        self.assertIn("Impressum", html)
        self.assertIn('alt="Objektfoto – Impressum"', html)


if __name__ == "__main__":
    unittest.main()
