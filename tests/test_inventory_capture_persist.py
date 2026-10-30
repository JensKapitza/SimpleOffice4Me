"""Persistent capture inbox: storage, reload, idempotency, assignment."""

from __future__ import annotations

import hashlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app
from app.capture_inbox_store import (
    CaptureInboxConflict,
    CaptureInboxIndexError,
    CaptureInboxStore,
)
from app.db import ensure_auth_database, get_db
from app.document_store import CONTROL_DIR
from app.inventory import InventoryEnrichmentStore
from app.object_store import ObjectStore
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX

JPEG_MIN = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\x00" * 64
)

PNG_RED_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


class CaptureInboxStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name) / "documents")
        self.store = CaptureInboxStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_create_persists_original_with_metadata(self) -> None:
        entry, duplicate = self.store.create("anna", PNG_1PX, ".png", "ab" * 16)
        self.assertFalse(duplicate)
        self.assertRegex(entry["entry_id"], r"[0-9a-f]{32}")
        self.assertEqual("anna", entry["actor"])
        self.assertEqual("unassigned", entry["status"])
        self.assertTrue(entry["created_at"])
        self.assertEqual(len(PNG_1PX), entry["original"]["size"])
        self.assertEqual(64, len(entry["original"]["sha256"]))
        path = self.store.media_path(entry["entry_id"], "anna")
        self.assertEqual(PNG_1PX, path.read_bytes())
        self.assertEqual("captured", entry["history"][0]["event"])

    def test_duplicate_upload_returns_existing_entry(self) -> None:
        first, _ = self.store.create("anna", PNG_1PX, ".png", "ab" * 16)
        replay, duplicate = self.store.create("anna", PNG_1PX, ".png", "ab" * 16)
        self.assertTrue(duplicate)
        self.assertEqual(first["entry_id"], replay["entry_id"])
        resend, duplicate = self.store.create("anna", PNG_1PX, ".png", "cd" * 16)
        self.assertTrue(duplicate)
        self.assertEqual(first["entry_id"], resend["entry_id"])
        self.assertEqual(1, len(self.store.list("anna")))

    def test_token_reuse_with_different_bytes_is_conflict_without_write(self) -> None:
        first, _ = self.store.create("anna", PNG_1PX, ".png", "ab" * 16)
        index_before = self.store.index_path.read_bytes()
        media_before = sorted(
            path.relative_to(self.store.media_directory).as_posix()
            for path in self.store.media_directory.rglob("*")
            if path.is_file()
        )
        with self.assertRaises(CaptureInboxConflict):
            self.store.create("anna", PNG_RED_1PX, ".png", "ab" * 16)
        self.assertEqual(index_before, self.store.index_path.read_bytes())
        media_after = sorted(
            path.relative_to(self.store.media_directory).as_posix()
            for path in self.store.media_directory.rglob("*")
            if path.is_file()
        )
        self.assertEqual(media_before, media_after)
        self.assertEqual(1, len(self.store.list("anna")))
        stored = self.store.get(first["entry_id"], "anna")
        assert stored is not None
        self.assertEqual(first["original"]["sha256"], stored["original"]["sha256"])
        self.assertEqual(
            PNG_1PX,
            self.store.media_path(first["entry_id"], "anna").read_bytes(),
        )
        replay, duplicate = self.store.create("anna", PNG_1PX, ".png", "ab" * 16)
        self.assertTrue(duplicate)
        self.assertEqual(first["entry_id"], replay["entry_id"])

    def test_same_token_different_actor_stays_isolated(self) -> None:
        first, _ = self.store.create("anna", PNG_1PX, ".png", "ab" * 16)
        other, duplicate = self.store.create("bob", PNG_RED_1PX, ".png", "ab" * 16)
        self.assertFalse(duplicate)
        self.assertNotEqual(first["entry_id"], other["entry_id"])
        self.assertEqual(
            PNG_RED_1PX,
            self.store.media_path(other["entry_id"], "bob").read_bytes(),
        )

    def test_actor_isolation(self) -> None:
        entry, _ = self.store.create("anna", PNG_1PX, ".png")
        self.assertIsNone(self.store.get(entry["entry_id"], "bob"))
        self.assertEqual([], self.store.list("bob"))
        with self.assertRaises(ValueError):
            self.store.media_path(entry["entry_id"], "bob")
        other, duplicate = self.store.create("bob", PNG_1PX, ".png")
        self.assertFalse(duplicate)
        self.assertNotEqual(entry["entry_id"], other["entry_id"])

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create("", PNG_1PX, ".png")
        with self.assertRaises(ValueError):
            self.store.create("anna", b"", ".png")
        with self.assertRaises(ValueError):
            self.store.create("anna", PNG_1PX, ".heic")
        with self.assertRaises(ValueError):
            self.store.create("anna", PNG_1PX, ".png", "not-a-token")
        self.assertIsNone(self.store.get("nope", "anna"))

    def test_analysis_and_assignment_keep_history(self) -> None:
        entry, _ = self.store.create("anna", PNG_1PX, ".png")
        updated = self.store.save_analysis(
            entry["entry_id"], "anna", {"ok": True, "kind": "object"})
        assert updated is not None
        self.assertEqual("object", updated["analysis"]["kind"])
        self.assertEqual(2, len(updated["history"]))
        foreign = self.store.save_analysis(
            entry["entry_id"], "bob", {"ok": True})
        self.assertIsNone(foreign)
        assigned = self.store.assign(
            entry["entry_id"], "anna", "object", "obj-1", "7", "Hammer")
        assert assigned is not None
        self.assertEqual("assigned", assigned["status"])
        self.assertEqual("obj-1", assigned["assignment"]["object_id"])
        self.assertEqual(3, len(assigned["history"]))
        replay = self.store.assign(
            entry["entry_id"], "anna", "object", "obj-1", "7", "Hammer")
        assert replay is not None
        self.assertEqual(3, len(replay["history"]))
        with self.assertRaises(ValueError):
            self.store.assign(entry["entry_id"], "anna", "task", "obj-1")

    def test_jpeg_magic_accepted(self) -> None:
        entry, _ = self.store.create("anna", JPEG_MIN, ".jpg")
        self.assertTrue(entry["entry_id"])


class CapturePersistRouteTests(unittest.TestCase):
    password = "capture-persist-test-password"
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

    def upload(self, data: bytes = PNG_1PX, request_id: str = "ab" * 16,
               client=None):
        who = client or self.client
        return who.post(
            "/inventory/capture/upload",
            data={
                "photo": (io.BytesIO(data), "foto.png"),
                "client_request_id": request_id,
                "_csrf_token": self.csrf(who),
            },
            content_type="multipart/form-data",
        )

    def test_upload_stores_and_survives_reload(self) -> None:
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["duplicate"])
        entry = payload["entry"]
        self.assertEqual("unassigned", entry["status"])
        self.assertIn("analysis", entry)
        self.assertNotIn("filename", str(entry))
        entry_id = entry["entry_id"]
        reloaded = self.client.get("/inventory/capture")
        self.assertEqual(reloaded.status_code, 200)
        self.assertIn(entry_id, reloaded.get_data(as_text=True))
        self.assertIn("unassigned", reloaded.get_data(as_text=True))
        self.assertIn("capture-entries", reloaded.get_data(as_text=True))

    def test_reload_page_without_entries(self) -> None:
        page = self.client.get("/inventory/capture")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="capture-entries"', page.get_data(as_text=True))
        self.assertIn("capture_inbox.js", page.get_data(as_text=True))

    def test_duplicate_upload_returns_same_entry(self) -> None:
        first = self.upload().get_json()["entry"]
        replay = self.upload(request_id="cd" * 16).get_json()
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(first["entry_id"], replay["entry"]["entry_id"])

    def test_upload_token_conflict_returns_409_without_mutation(self) -> None:
        first = self.upload().get_json()["entry"]
        entry_id = first["entry_id"]
        store = CaptureInboxStore(self.documents)
        index_path = Path(self.documents) / CONTROL_DIR / "inventory" / "capture-inbox.json"
        index_before = index_path.read_bytes()
        media_before = sorted(
            path.relative_to(store.media_directory).as_posix()
            for path in store.media_directory.rglob("*")
            if path.is_file()
        )
        analysis_before = dict(store.get(entry_id, "anna") or {}).get("analysis", {})
        conflict = self.upload(data=PNG_RED_1PX, request_id="ab" * 16)
        self.assertEqual(conflict.status_code, 409)
        payload = conflict.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("Erfassungskennung", payload["error"])
        self.assertEqual(index_before, index_path.read_bytes())
        media_after = sorted(
            path.relative_to(store.media_directory).as_posix()
            for path in store.media_directory.rglob("*")
            if path.is_file()
        )
        self.assertEqual(media_before, media_after)
        stored = store.get(entry_id, "anna")
        assert stored is not None
        self.assertEqual(PNG_1PX, store.media_path(entry_id, "anna").read_bytes())
        self.assertEqual(analysis_before, stored.get("analysis", {}))
        self.assertEqual(1, len(store.list("anna")))
        replay = self.upload().get_json()
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(entry_id, replay["entry"]["entry_id"])
        foreign = self.upload(
            data=PNG_RED_1PX, request_id="ab" * 16, client=self.bob)
        self.assertEqual(foreign.status_code, 200)
        self.assertFalse(foreign.get_json()["duplicate"])

    def test_preview_owner_only_no_store(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        preview = self.client.get(f"/inventory/capture/{entry_id}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual("image/png", preview.content_type)
        self.assertEqual("no-store", preview.headers["Cache-Control"])
        self.assertEqual(PNG_1PX, preview.data)
        preview.close()
        foreign = self.bob.get(f"/inventory/capture/{entry_id}/preview")
        self.assertEqual(foreign.status_code, 404)
        missing = self.client.get(
            "/inventory/capture/ffffffffffffffffffffffffffffffff/preview")
        self.assertEqual(missing.status_code, 404)

    def test_assign_links_object_idempotently(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        with app.app_context():
            item = ObjectStore(app.config["DOCUMENT_ROOT"]).create(
                {"name": "Hammer", "type": "object"}, "anna")
        for _ in range(2):
            response = self.client.post(
                f"/inventory/capture/{entry_id}/assign",
                json={"kind": "object", "object_id": item["object_id"]},
                headers={"X-CSRF-Token": self.csrf()},
            )
            self.assertEqual(response.status_code, 200)
            updated = response.get_json()["entry"]
            self.assertEqual("assigned", updated["status"])
            self.assertEqual(item["object_id"],
                             updated["assignment"]["object_id"])
        stored = CaptureInboxStore(self.documents).get(entry_id, "anna")
        assert stored is not None
        self.assertEqual(PNG_1PX, CaptureInboxStore(self.documents)
                         .media_path(entry_id, "anna").read_bytes())
        kinds = [event["event"] for event in stored["history"]]
        self.assertEqual(["captured", "analyzed", "assigned"], kinds)

    def test_assign_attaches_original_to_target_without_duplicate(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        with app.app_context():
            item = ObjectStore(app.config["DOCUMENT_ROOT"]).create(
                {"name": "Hammer", "type": "object"}, "anna")

        def assign():
            return self.client.post(
                f"/inventory/capture/{entry_id}/assign",
                json={"kind": "object", "object_id": item["object_id"]},
                headers={"X-CSRF-Token": self.csrf()},
            )

        def attached_digests():
            photos = InventoryEnrichmentStore(self.documents).object_meta(
                item["object_id"]).get("photos", [])
            return [photo["sha256"] for photo in photos]

        first = assign()
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()["attached"])
        self.assertEqual([], first.get_json()["notices"])
        self.assertEqual([hashlib.sha256(PNG_1PX).hexdigest()],
                         attached_digests())
        second = assign()
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.get_json()["attached"])
        self.assertEqual(1, len(attached_digests()))
        self.assertEqual(
            PNG_1PX,
            CaptureInboxStore(self.documents)
            .media_path(entry_id, "anna").read_bytes(),
        )

    def test_duplicate_adopt_can_link_existing_object_with_photo(self) -> None:
        created = self.client.post(
            "/inventory/items",
            data={
                "title": "Hammer", "barcode": "4006381333931",
                "item_type": "object", "response_format": "json",
                "capture_request_id": "11" * 16, "library_return": "",
                "_csrf_token": self.csrf(),
            },
        )
        self.assertTrue(created.get_json()["ok"])
        self.assertTrue(created.get_json()["saved"])
        object_id = created.get_json()["object_id"]
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        adopt = self.client.post(
            "/inventory/items",
            data={
                "title": "Hammer", "barcode": "4006381333931",
                "item_type": "object", "response_format": "json",
                "capture_request_id": "22" * 16, "library_return": "",
                "_csrf_token": self.csrf(),
            },
        )
        payload = adopt.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["duplicate"])
        self.assertEqual(object_id, payload["object_id"])
        linked = self.client.post(
            f"/inventory/capture/{entry_id}/assign",
            json={"kind": "object", "object_id": payload["object_id"]},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(linked.status_code, 200)
        self.assertTrue(linked.get_json()["ok"])
        self.assertTrue(linked.get_json()["attached"])
        photos = InventoryEnrichmentStore(self.documents).object_meta(
            object_id).get("photos", [])
        self.assertEqual(1, len(photos))
        self.assertEqual(
            hashlib.sha256(PNG_1PX).hexdigest(), photos[0]["sha256"])

    def test_assign_validates_target_and_owner(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        bad_target = self.client.post(
            f"/inventory/capture/{entry_id}/assign",
            json={"kind": "object", "object_id": "missing-id"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(bad_target.status_code, 400)
        bad_kind = self.client.post(
            f"/inventory/capture/{entry_id}/assign",
            json={"kind": "task", "object_id": "whatever"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(bad_kind.status_code, 400)
        foreign = self.bob.post(
            f"/inventory/capture/{entry_id}/assign",
            json={"kind": "object", "object_id": "whatever"},
            headers={"X-CSRF-Token": self.csrf(self.bob)},
        )
        self.assertEqual(foreign.status_code, 404)
        missing = self.client.post(
            "/inventory/capture/ffffffffffffffffffffffffffffffff/assign",
            json={"kind": "object", "object_id": "whatever"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(missing.status_code, 404)

    def test_reanalyze_updates_stored_entry(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        with mock.patch("app.inventory_capture.capture_ml_label") as ml_label:
            ml_label.return_value = {
                "available": True, "label": "Hammer", "item_type": "tool",
                "isbn": "", "provider": "local",
                "provider_label": "Ollama (lokal)", "model": "m",
                "error": "",
            }
            response = self.client.post(
                f"/inventory/capture/{entry_id}/analyze",
                data={"_csrf_token": self.csrf()},
            )
        self.assertEqual(response.status_code, 200)
        analysis = response.get_json()["entry"]["analysis"]
        self.assertEqual("Hammer", analysis["ml"]["label"])
        missing = self.client.post(
            "/inventory/capture/ffffffffffffffffffffffffffffffff/analyze",
            data={"_csrf_token": self.csrf()},
        )
        self.assertEqual(missing.status_code, 404)

    def test_invalid_uploads_rejected(self) -> None:
        response = self.upload(data=b"kein-bild")
        self.assertEqual(response.status_code, 400)
        response = self.upload(request_id="bad-token")
        self.assertEqual(response.status_code, 400)


class CaptureFailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name) / "documents")
        self.store = CaptureInboxStore(self.root)
        self.index = Path(self.root) / CONTROL_DIR / "inventory" / "capture-inbox.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_index_is_empty_inbox(self) -> None:
        self.assertEqual([], self.store.list("anna"))
        self.assertIsNone(self.store.get("ab" * 16, "anna"))

    def test_corrupt_index_raises_and_stays_byte_identical(self) -> None:
        entry, _ = self.store.create("anna", PNG_1PX, ".png")
        media_before = sorted(
            path.name for path in self.store.media_directory.rglob("*"))
        broken = self.index.read_bytes() + b"\n{kaputt"
        self.index.write_bytes(broken)
        with self.assertRaises(CaptureInboxIndexError):
            self.store.list("anna")
        with self.assertRaises(CaptureInboxIndexError):
            self.store.get(entry["entry_id"], "anna")
        with self.assertRaises(CaptureInboxIndexError):
            self.store.create("anna", JPEG_MIN, ".jpg")
        with self.assertRaises(CaptureInboxIndexError):
            self.store.save_analysis(entry["entry_id"], "anna", {})
        with self.assertRaises(CaptureInboxIndexError):
            self.store.assign(entry["entry_id"], "anna", "object", "obj-1")
        with self.assertRaises(CaptureInboxIndexError):
            self.store.media_path(entry["entry_id"], "anna")
        self.assertEqual(broken, self.index.read_bytes())
        media_after = sorted(
            path.name for path in self.store.media_directory.rglob("*"))
        self.assertEqual(media_before, media_after)

    def test_wrong_shape_index_raises(self) -> None:
        self.index.parent.mkdir(parents=True, exist_ok=True)
        for shape in (b"[1, 2]", b'{"entries": []}', b'{"entries": null}'):
            self.index.write_bytes(shape)
            with self.assertRaises(CaptureInboxIndexError, msg=shape):
                self.store.list("anna")
            self.assertEqual(shape, self.index.read_bytes())


class CaptureCorruptIndexRouteTests(CapturePersistRouteTests):
    def corrupt(self) -> bytes:
        path = Path(self.documents) / CONTROL_DIR / "inventory" / "capture-inbox.json"
        broken = path.read_bytes() + b"\n{kaputt"
        path.write_bytes(broken)
        return broken

    def index_bytes(self) -> bytes:
        path = Path(self.documents) / CONTROL_DIR / "inventory" / "capture-inbox.json"
        return path.read_bytes()

    def test_upload_refused_and_index_preserved(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        broken = self.corrupt()
        response = self.upload(data=JPEG_MIN, request_id="ee" * 16)
        self.assertEqual(response.status_code, 503)
        self.assertIn("nicht verfügbar", response.get_json()["error"])
        self.assertEqual(broken, self.index_bytes())
        store = CaptureInboxStore(self.documents)
        with self.assertRaises(CaptureInboxIndexError):
            store.get(entry_id, "anna")

    def test_page_shows_error_banner(self) -> None:
        self.upload()
        self.corrupt()
        page = self.client.get("/inventory/capture")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("vorübergehend nicht verfügbar", body)
        self.assertIn('id="capture-entries">[]<', body)

    def test_detail_routes_fail_closed(self) -> None:
        self.upload()
        self.corrupt()
        fake = "f" * 32
        preview = self.client.get(f"/inventory/capture/{fake}/preview")
        self.assertEqual(preview.status_code, 503)
        self.assertIn("vorübergehend nicht verfügbar",
                      preview.get_data(as_text=True))
        preview.close()
        assign = self.client.post(
            f"/inventory/capture/{fake}/assign",
            json={"kind": "object", "object_id": "x"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(assign.status_code, 503)
        self.assertIn("nicht verfügbar", assign.get_json()["error"])
        reanalyze = self.client.post(
            f"/inventory/capture/{fake}/analyze",
            data={"_csrf_token": self.csrf()},
        )
        self.assertEqual(reanalyze.status_code, 503)
        self.assertIn("nicht verfügbar", reanalyze.get_json()["error"])

    def test_kind_must_match_target(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        with app.app_context():
            store = ObjectStore(app.config["DOCUMENT_ROOT"])
            book = store.create({"name": "Roman", "type": "book"}, "anna")
            thing = store.create({"name": "Hammer", "type": "object"}, "anna")

        def assign(kind: str, object_id: str):
            return self.client.post(
                f"/inventory/capture/{entry_id}/assign",
                json={"kind": kind, "object_id": object_id},
                headers={"X-CSRF-Token": self.csrf()},
            )

        mismatch_book = assign("book", thing["object_id"])
        self.assertEqual(mismatch_book.status_code, 400)
        self.assertIn("Buch erwartet", mismatch_book.get_json()["error"])
        mismatch_object = assign("object", book["object_id"])
        self.assertEqual(mismatch_object.status_code, 400)
        self.assertIn("Gegenstand erwartet", mismatch_object.get_json()["error"])
        match = assign("book", book["object_id"])
        self.assertEqual(match.status_code, 200)
        self.assertEqual("assigned", match.get_json()["entry"]["status"])

    def test_buch_type_counts_as_book(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        with app.app_context():
            book = ObjectStore(app.config["DOCUMENT_ROOT"]).create(
                {"name": "Roman", "type": "Buch"}, "anna")
        response = self.client.post(
            f"/inventory/capture/{entry_id}/assign",
            json={"kind": "book", "object_id": book["object_id"]},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 200)

    def test_reassign_keeps_original_and_history(self) -> None:
        entry_id = self.upload().get_json()["entry"]["entry_id"]
        with app.app_context():
            store = ObjectStore(app.config["DOCUMENT_ROOT"])
            first = store.create({"name": "Hammer", "type": "object"}, "anna")
            second = store.create({"name": "Zange", "type": "object"}, "anna")

        def assign(object_id: str):
            return self.client.post(
                f"/inventory/capture/{entry_id}/assign",
                json={"kind": "object", "object_id": object_id},
                headers={"X-CSRF-Token": self.csrf()},
            )

        self.assertEqual(assign(first["object_id"]).status_code, 200)
        self.assertEqual(assign(second["object_id"]).status_code, 200)
        store = CaptureInboxStore(self.documents)
        stored = store.get(entry_id, "anna")
        assert stored is not None
        self.assertEqual("assigned", stored["status"])
        self.assertEqual(second["object_id"],
                         stored["assignment"]["object_id"])
        self.assertEqual(PNG_1PX, store.media_path(entry_id, "anna").read_bytes())
        events = [event["event"] for event in stored["history"]]
        self.assertEqual(["captured", "analyzed", "assigned", "assigned"], events)
        self.assertEqual(first["object_id"],
                         stored["history"][-1]["previous"]["object_id"])


if __name__ == "__main__":
    unittest.main()
