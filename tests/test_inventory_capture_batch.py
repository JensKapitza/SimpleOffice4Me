"""Book-group capture batches: grouping, combined review, one-book adoption.

Issue #658: several phone photos of the same physical book (title page,
imprint, back cover) must stay grouped, be analyzed together, and be
reviewable/correctable before saving; rapid successive books must never
mix photos.
"""

from __future__ import annotations

import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app
from app.capture_analysis import combine_batch_analyses
from app.capture_inbox_store import (
    MAX_BATCH_PHOTOS,
    CaptureInboxConflict,
    CaptureInboxIndexError,
    CaptureInboxStore,
)
from app.db import ensure_auth_database, get_db
from app.document_store import CONTROL_DIR
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX

PNG_RED_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_BLUE_1PX = PNG_1PX + b"\x00blue-padding"

BATCH_A = "aa" * 16
BATCH_B = "bb" * 16

ISBN_DEMO = "9783161484100"
ISBN_OTHER = "9780306406157"


def _photo_payload(photo_id: str, **overrides) -> dict:
    payload = {
        "ok": True,
        "photo_id": photo_id,
        "sha256": "0" * 64,
        "kind": "book",
        "kind_reason": "ISBN im Foto erkannt",
        "needs_name": False,
        "isbn": ISBN_DEMO,
        "prefill": {
            "isbn": ISBN_DEMO,
            "title": "Der Musterroman",
            "authors": ["Max Muster"],
            "year": "2021",
            "publisher": "Verlag Demo",
            "title_lines": ["Der Musterroman", "Max Muster"],
            "metadata_source": "ocr",
        },
        "ocr": {"engine": "rapidocr", "status": "completed",
                "characters": 60, "confidence": 90, "error": ""},
        "ml": {"available": False, "label": "", "item_type": "",
               "provider": "", "provider_label": "", "model": "",
               "error": "ML: Ollama nicht erreichbar"},
        "duplicates": [],
        "missing_fields": [],
    }
    payload.update(overrides)
    return payload


class BatchCombineTests(unittest.TestCase):
    def test_same_isbn_merges_first_values_win(self) -> None:
        first = _photo_payload("photo-1")
        second = _photo_payload("photo-2")
        second["prefill"] = {
            "isbn": ISBN_DEMO, "title": "", "authors": ["Zweite Feder"],
            "year": "", "publisher": "",
            "title_lines": ["Klappentext-Zeile"], "metadata_source": "ocr",
        }
        combined = combine_batch_analyses([first, second])
        self.assertEqual(2, combined["photo_count"])
        self.assertEqual(2, combined["analyzed_count"])
        self.assertEqual(ISBN_DEMO, combined["isbn"])
        self.assertFalse(combined["isbn_conflict"])
        self.assertEqual("Der Musterroman", combined["prefill"]["title"])
        self.assertEqual(
            ["Max Muster", "Zweite Feder"], combined["prefill"]["authors"])
        self.assertEqual("2021", combined["prefill"]["year"])
        self.assertIn("Klappentext-Zeile",
                      combined["prefill"]["title_lines"])
        self.assertEqual("book", combined["kind"])
        self.assertEqual([], combined["missing_fields"])
        self.assertEqual(2, len(combined["photos"]))
        self.assertEqual("photo-1", combined["photos"][0]["photo_id"])

    def test_conflicting_isbns_require_user_choice(self) -> None:
        first = _photo_payload("photo-1")
        second = _photo_payload("photo-2", isbn=ISBN_OTHER)
        second["prefill"] = dict(second["prefill"], isbn=ISBN_OTHER)
        combined = combine_batch_analyses([first, second])
        self.assertEqual("", combined["isbn"])
        self.assertTrue(combined["isbn_conflict"])
        self.assertEqual([ISBN_DEMO, ISBN_OTHER],
                         combined["isbn_candidates"])
        self.assertIn("isbn", combined["missing_fields"])
        self.assertTrue(combined["warnings"])
        self.assertEqual("book", combined["kind"])

    def test_duplicates_dedupe_and_models_stay_honest(self) -> None:
        dup = {"object_id": "obj-1", "display_id": "7",
               "name": "Der Musterroman", "url": "/inventory/obj-1"}
        first = _photo_payload("photo-1", duplicates=[dup])
        first["ml"] = {"available": True, "label": "Buch",
                       "item_type": "book", "provider": "local",
                       "provider_label": "Meta-Modell lokal via Ollama",
                       "model": "llama3.2-vision:11b", "error": ""}
        second = _photo_payload("photo-2", duplicates=[dict(dup)])
        combined = combine_batch_analyses([first, second])
        self.assertEqual(1, len(combined["duplicates"]))
        self.assertEqual(["llama3.2-vision:11b"], combined["ml_models"])
        self.assertTrue(combined["photos"][0]["ml_available"])
        self.assertFalse(combined["photos"][1]["ml_available"])

    def test_unanalyzed_photos_are_reported_not_dropped(self) -> None:
        combined = combine_batch_analyses(
            [_photo_payload("photo-1"), {"ok": False}])
        self.assertEqual(2, combined["photo_count"])
        self.assertEqual(1, combined["analyzed_count"])
        self.assertTrue(combined["warnings"])

    def test_object_fallback_without_book_evidence(self) -> None:
        payload = _photo_payload("photo-1", kind="object",
                                 kind_reason="manuell einordnen",
                                 needs_name=True, isbn="")
        payload["prefill"] = {"isbn": "", "name": ""}
        combined = combine_batch_analyses([payload])
        self.assertEqual("object", combined["kind"])
        self.assertEqual(["name"], combined["missing_fields"])


class BatchStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.temp.name) / "documents")
        self.store = CaptureInboxStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_create_with_batch_and_list_in_capture_order(self) -> None:
        first, _ = self.store.create(
            "anna", b"fake-title-page", ".png", "a1" * 16, BATCH_A)
        second, _ = self.store.create(
            "anna", b"fake-imprint", ".png", "a2" * 16, BATCH_A)
        self.assertEqual(BATCH_A, first["batch_id"])
        rows = self.store.list_batch("anna", BATCH_A)
        self.assertEqual(
            [first["entry_id"], second["entry_id"]],
            [row["entry_id"] for row in rows])
        created = [row["created_at"] for row in rows]
        self.assertEqual(sorted(created), created)

    def test_invalid_batch_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create("anna", b"fake", ".png", batch_id="bad")
        with self.assertRaises(ValueError):
            self.store.list_batch("anna", "bad")
        entry, _ = self.store.create("anna", b"fake", ".png")
        with self.assertRaises(ValueError):
            self.store.set_batch(entry["entry_id"], "anna", "bad")

    def test_content_dedup_never_mixes_batches(self) -> None:
        first, dup_a = self.store.create(
            "anna", PNG_1PX, ".png", "a1" * 16, BATCH_A)
        self.assertFalse(dup_a)
        other, dup_b = self.store.create(
            "anna", PNG_1PX, ".png", "b1" * 16, BATCH_B)
        self.assertFalse(dup_b)
        self.assertNotEqual(first["entry_id"], other["entry_id"])
        replay, dup_replay = self.store.create(
            "anna", PNG_1PX, ".png", "a2" * 16, BATCH_A)
        self.assertTrue(dup_replay)
        self.assertEqual(first["entry_id"], replay["entry_id"])
        self.assertEqual(1, len(self.store.list_batch("anna", BATCH_A)))
        self.assertEqual(1, len(self.store.list_batch("anna", BATCH_B)))

    def test_token_replay_keeps_stored_batch(self) -> None:
        first, _ = self.store.create(
            "anna", PNG_1PX, ".png", "a1" * 16, BATCH_A)
        replay, duplicate = self.store.create(
            "anna", PNG_1PX, ".png", "a1" * 16, BATCH_B)
        self.assertTrue(duplicate)
        self.assertEqual(first["entry_id"], replay["entry_id"])
        self.assertEqual(BATCH_A, replay["batch_id"])
        self.assertEqual([], self.store.list_batch("anna", BATCH_B))

    def test_token_conflict_still_guarded_with_batch(self) -> None:
        self.store.create("anna", PNG_1PX, ".png", "a1" * 16, BATCH_A)
        with self.assertRaises(CaptureInboxConflict):
            self.store.create(
                "anna", PNG_RED_1PX, ".png", "a1" * 16, BATCH_A)

    def test_same_batch_id_stays_actor_scoped(self) -> None:
        mine, _ = self.store.create(
            "anna", PNG_1PX, ".png", "a1" * 16, BATCH_A)
        foreign, duplicate = self.store.create(
            "bob", PNG_RED_1PX, ".png", "b1" * 16, BATCH_A)
        self.assertFalse(duplicate)
        self.assertEqual(
            [mine["entry_id"]],
            [row["entry_id"] for row in self.store.list_batch(
                "anna", BATCH_A)])
        self.assertEqual(
            [foreign["entry_id"]],
            [row["entry_id"] for row in self.store.list_batch(
                "bob", BATCH_A)])

    def test_set_batch_moves_and_ungroups_with_history(self) -> None:
        entry, _ = self.store.create("anna", b"fake-photo", ".png")
        moved = self.store.set_batch(entry["entry_id"], "anna", BATCH_A)
        assert moved is not None
        self.assertEqual(BATCH_A, moved["batch_id"])
        self.assertEqual("regrouped", moved["history"][-1]["event"])
        self.assertEqual("", moved["history"][-1]["previous_batch_id"])
        self.assertEqual(b"fake-photo", self.store.media_path(
            entry["entry_id"], "anna").read_bytes())
        replay = self.store.set_batch(entry["entry_id"], "anna", BATCH_A)
        assert replay is not None
        self.assertEqual(len(moved["history"]), len(replay["history"]))
        ungrouped = self.store.set_batch(entry["entry_id"], "anna", "")
        assert ungrouped is not None
        self.assertEqual("", ungrouped["batch_id"])
        self.assertEqual(BATCH_A,
                         ungrouped["history"][-1]["previous_batch_id"])
        self.assertIsNone(
            self.store.set_batch(entry["entry_id"], "bob", BATCH_B))

    def test_batch_limit_enforced(self) -> None:
        for index in range(MAX_BATCH_PHOTOS):
            self.store.create(
                "anna", f"fake-{index}".encode(), ".png", batch_id=BATCH_A)
        with self.assertRaises(ValueError) as raised:
            self.store.create(
                "anna", b"one-too-many", ".png", batch_id=BATCH_A)
        self.assertIn("voll", str(raised.exception))
        single, _ = self.store.create("anna", b"single", ".png")
        with self.assertRaises(ValueError):
            self.store.set_batch(single["entry_id"], "anna", BATCH_A)
        self.assertEqual(MAX_BATCH_PHOTOS,
                         len(self.store.list_batch("anna", BATCH_A)))

    def test_batch_ops_fail_closed_on_corrupt_index(self) -> None:
        entry, _ = self.store.create(
            "anna", PNG_1PX, ".png", batch_id=BATCH_A)
        index_path = (Path(self.root) / CONTROL_DIR / "inventory"
                      / "capture-inbox.json")
        broken = index_path.read_bytes() + b"\n{kaputt"
        index_path.write_bytes(broken)
        with self.assertRaises(CaptureInboxIndexError):
            self.store.list_batch("anna", BATCH_A)
        with self.assertRaises(CaptureInboxIndexError):
            self.store.set_batch(entry["entry_id"], "anna", BATCH_B)
        self.assertEqual(broken, index_path.read_bytes())


class BatchRouteTests(unittest.TestCase):
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

    def save_analyses(self, entries_payloads: list[tuple[str, dict]]) -> None:
        store = CaptureInboxStore(self.documents)
        for entry_id, payload in entries_payloads:
            updated = store.save_analysis(entry_id, "anna", payload)
            self.assertIsNotNone(updated)

    def adopt(self, batch_id: str, fields: dict, client=None):
        who = client or self.client
        form = dict(fields)
        form["_csrf_token"] = self.csrf(who)
        return who.post(
            f"/inventory/capture/batch/{batch_id}/adopt",
            data=form,
            content_type="multipart/form-data",
        )

    def test_meta_model_pin_and_mock_honesty(self) -> None:
        from app.contextflow.vision import (
            META_MODEL_API_MODEL,
            MockBlockedError,
            suggest_inventory_object,
        )

        self.assertEqual("muse-spark-1.3-contributor", META_MODEL_API_MODEL)
        with self.assertRaises(MockBlockedError):
            suggest_inventory_object(
                b"fake", "image/png", {}, {"provider": "mock"}, False)

    def test_consent_without_demo_root_stays_honest(self) -> None:
        from app.inventory_capture import capture_ml_label

        settings = {
            "provider": "meta",
            "meta_consent": True,
            "meta_api_key": "test-only-dummy-key",
            "meta_endpoint": "https://api.meta.ai/v1/responses",
            "meta_model": "muse-spark-1.3-contributor",
        }
        result = capture_ml_label(
            b"fake-png", "", settings,
            consent_meta=True, root=self.documents)
        self.assertFalse(result["available"])
        self.assertEqual("", result["model"])
        self.assertIn("demo root", result["error"].lower())

    def test_upload_with_batch_and_review_groups(self) -> None:
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        self.assertEqual(BATCH_A, first["batch_id"])
        self.assertIn("batch_url", first)
        self.save_analyses([
            (first["entry_id"], _photo_payload(first["entry_id"])),
            (second["entry_id"], _photo_payload(second["entry_id"])),
        ])
        response = self.client.get(f"/inventory/capture/batch/{BATCH_A}")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(BATCH_A, payload["batch_id"])
        self.assertEqual(
            [first["entry_id"], second["entry_id"]],
            [entry["entry_id"] for entry in payload["entries"]])
        self.assertEqual(ISBN_DEMO, payload["combined"]["isbn"])
        self.assertEqual("Der Musterroman",
                         payload["combined"]["prefill"]["title"])
        self.assertIn("analyze_url", payload)
        self.assertIn("adopt_url", payload)

    def test_rapid_successive_books_never_mix(self) -> None:
        a1 = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        a2 = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        b1 = self.upload(PNG_1PX, BATCH_B).get_json()["entry"]
        b2 = self.upload(PNG_BLUE_1PX, BATCH_B).get_json()["entry"]
        # Same bytes in another batch are a new book, not a dedup hit.
        self.assertNotEqual(a1["entry_id"], b1["entry_id"])
        other = _photo_payload(b1["entry_id"], isbn=ISBN_OTHER)
        other["prefill"] = dict(
            other["prefill"], isbn=ISBN_OTHER, title="Anderes Buch")
        other2 = _photo_payload(b2["entry_id"], isbn=ISBN_OTHER)
        other2["prefill"] = dict(
            other2["prefill"], isbn=ISBN_OTHER, title="Anderes Buch")
        self.save_analyses([
            (a1["entry_id"], _photo_payload(a1["entry_id"])),
            (a2["entry_id"], _photo_payload(a2["entry_id"])),
            (b1["entry_id"], other),
            (b2["entry_id"], other2),
        ])
        group_a = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        group_b = self.client.get(
            f"/inventory/capture/batch/{BATCH_B}").get_json()
        ids_a = {entry["entry_id"] for entry in group_a["entries"]}
        ids_b = {entry["entry_id"] for entry in group_b["entries"]}
        self.assertEqual({a1["entry_id"], a2["entry_id"]}, ids_a)
        self.assertEqual({b1["entry_id"], b2["entry_id"]}, ids_b)
        self.assertTrue(ids_a.isdisjoint(ids_b))
        self.assertEqual(ISBN_DEMO, group_a["combined"]["isbn"])
        self.assertEqual(ISBN_OTHER, group_b["combined"]["isbn"])

    def test_batch_review_access_control(self) -> None:
        mine = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.assertEqual(
            404, self.bob.get(
                f"/inventory/capture/batch/{BATCH_A}").status_code)
        self.assertEqual(
            404, self.client.get(
                "/inventory/capture/batch/" + "cc" * 16).status_code)
        self.assertEqual(
            400, self.client.get(
                "/inventory/capture/batch/not-a-batch").status_code)
        foreign = self.upload(
            PNG_RED_1PX, BATCH_A, client=self.bob).get_json()["entry"]
        self.assertNotEqual(mine["entry_id"], foreign["entry_id"])
        mine_only = self.client.get(
            f"/inventory/capture/batch/{BATCH_A}").get_json()
        self.assertEqual(1, len(mine_only["entries"]))

    def test_invalid_batch_upload_rejected(self) -> None:
        response = self.upload(PNG_1PX, "not-a-batch")
        self.assertEqual(400, response.status_code)
        self.assertIn("Buchgruppe", response.get_json()["error"])

    def test_upload_rejects_oversize_photo_without_side_effects(self) -> None:
        from app.inventory import InventoryEnrichmentStore, MAX_PHOTO_BYTES
        from app.object_store import ObjectStore

        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_PHOTO_BYTES + 1 - 8)
        self.assertEqual(MAX_PHOTO_BYTES + 1, len(big))
        response = self.upload(big, BATCH_A)
        self.assertEqual(400, response.status_code)
        self.assertIn("12 MiB", response.get_json()["error"])
        store = CaptureInboxStore(self.documents)
        self.assertEqual([], store.list("anna"))
        self.assertEqual([], store.list_batch("anna", BATCH_A))
        media_root = (Path(self.documents) / CONTROL_DIR / "inventory"
                      / "media" / "inbox")
        leftovers = ([path for path in media_root.rglob("*")
                      if path.is_file()] if media_root.exists() else [])
        self.assertEqual([], leftovers)
        self.assertEqual(
            {}, InventoryEnrichmentStore(self.documents).object_metas())
        self.assertEqual([], ObjectStore(self.documents).objects())

    def test_upload_rejects_unsupported_or_mismatched_photo(self) -> None:
        from app.inventory import InventoryEnrichmentStore
        from app.object_store import ObjectStore

        cases = [
            (b"dies-ist-kein-bild" * 64, "foto.png", "image/png"),
            (b"GIF89a" + b"\x00" * 64, "foto.jpg", "image/jpeg"),
        ]
        for data, filename, mimetype in cases:
            with self.subTest(filename=filename, mimetype=mimetype):
                form = {
                    "photo": (io.BytesIO(data), filename, mimetype),
                    "client_request_id": self.next_token(),
                    "_csrf_token": self.csrf(),
                    "batch_id": BATCH_A,
                }
                response = self.client.post(
                    "/inventory/capture/upload",
                    data=form,
                    content_type="multipart/form-data",
                )
                self.assertEqual(400, response.status_code)
                self.assertIn("JPEG, PNG", response.get_json()["error"])
                store = CaptureInboxStore(self.documents)
                self.assertEqual([], store.list("anna"))
                self.assertEqual([], store.list_batch("anna", BATCH_A))
        media_root = (Path(self.documents) / CONTROL_DIR / "inventory"
                      / "media" / "inbox")
        leftovers = ([path for path in media_root.rglob("*")
                      if path.is_file()] if media_root.exists() else [])
        self.assertEqual([], leftovers)
        self.assertEqual(
            {}, InventoryEnrichmentStore(self.documents).object_metas())
        self.assertEqual([], ObjectStore(self.documents).objects())

    def test_batch_analyze_saves_per_photo_and_combines(self) -> None:
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]

        def fake_analyze(raw: bytes, photo_id: str, **kwargs):
            payload = _photo_payload(photo_id)
            if photo_id == second["entry_id"]:
                payload["prefill"] = dict(
                    payload["prefill"], title="",
                    title_lines=["Rückseite Zeile"])
            return payload

        with mock.patch("app.inventory_capture.analyze_photo",
                        side_effect=fake_analyze):
            response = self.client.post(
                f"/inventory/capture/batch/{BATCH_A}/analyze",
                data={"_csrf_token": self.csrf()},
            )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual({}, payload["analysis_errors"])
        self.assertFalse(payload["consent_meta"])
        self.assertEqual("Der Musterroman",
                         payload["combined"]["prefill"]["title"])
        self.assertIn("Rückseite Zeile",
                      payload["combined"]["prefill"]["title_lines"])
        stored = CaptureInboxStore(self.documents)
        self.assertTrue(stored.get(
            first["entry_id"], "anna")["analysis"]["ok"])

    def test_batch_analyze_keeps_previous_on_single_failure(self) -> None:
        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        kept = _photo_payload(second["entry_id"])
        self.save_analyses([
            (first["entry_id"], _photo_payload(first["entry_id"])),
            (second["entry_id"], kept),
        ])

        def fake_analyze(raw: bytes, photo_id: str, **kwargs):
            if photo_id == first["entry_id"]:
                raise ValueError("OCR ist abgestürzt")
            return _photo_payload(photo_id)

        with mock.patch("app.inventory_capture.analyze_photo",
                        side_effect=fake_analyze):
            response = self.client.post(
                f"/inventory/capture/batch/{BATCH_A}/analyze",
                data={"_csrf_token": self.csrf()},
            )
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn(first["entry_id"], payload["analysis_errors"])
        stored = CaptureInboxStore(self.documents).get(
            first["entry_id"], "anna")
        assert stored is not None
        self.assertEqual(ISBN_DEMO, stored["analysis"]["isbn"])
        self.assertEqual(2, payload["combined"]["photo_count"])

    def test_move_corrects_grouping(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        moved = self.client.post(
            f"/inventory/capture/{entry['entry_id']}/batch",
            json={"batch_id": BATCH_B},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(200, moved.status_code)
        self.assertEqual(BATCH_B, moved.get_json()["entry"]["batch_id"])
        self.assertEqual(
            404, self.client.get(
                f"/inventory/capture/batch/{BATCH_A}").status_code)
        self.assertEqual(
            1, len(self.client.get(
                f"/inventory/capture/batch/{BATCH_B}").get_json()["entries"]))
        ungrouped = self.client.post(
            f"/inventory/capture/{entry['entry_id']}/batch",
            json={"batch_id": ""},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual("", ungrouped.get_json()["entry"]["batch_id"])
        bad = self.client.post(
            f"/inventory/capture/{entry['entry_id']}/batch",
            json={"batch_id": "bad"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(400, bad.status_code)
        foreign = self.bob.post(
            f"/inventory/capture/{entry['entry_id']}/batch",
            json={"batch_id": BATCH_B},
            headers={"X-CSRF-Token": self.csrf(self.bob)},
        )
        self.assertEqual(404, foreign.status_code)

    def test_adopt_creates_one_book_with_all_photos(self) -> None:
        from app.inventory import InventoryEnrichmentStore

        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        second = self.upload(PNG_RED_1PX, BATCH_A).get_json()["entry"]
        self.save_analyses([
            (first["entry_id"], _photo_payload(first["entry_id"])),
            (second["entry_id"], _photo_payload(second["entry_id"])),
        ])
        token = self.next_token()
        response = self.adopt(BATCH_A, {
            "title": "Korrigierter Titel",
            "authors": "Max Muster",
            "published_date": "2021",
            "publisher": "Verlag Demo",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": token,
        })
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["saved"])
        self.assertFalse(payload["duplicate"])
        self.assertEqual("Korrigierter Titel", payload["name"])
        self.assertEqual(2, payload["attached"])
        self.assertEqual(2, payload["assigned_count"])
        meta = InventoryEnrichmentStore(self.documents).object_meta(
            payload["object_id"])
        self.assertEqual(2, len(meta.get("photos", [])))
        attached_sizes = sorted(
            photo["size"] for photo in meta["photos"])
        self.assertEqual(
            sorted([len(PNG_1PX), len(PNG_RED_1PX)]), attached_sizes)
        store = CaptureInboxStore(self.documents)
        for entry_id in (first["entry_id"], second["entry_id"]):
            stored = store.get(entry_id, "anna")
            assert stored is not None
            self.assertEqual("assigned", stored["status"])
            self.assertEqual(payload["object_id"],
                             stored["assignment"]["object_id"])
            self.assertEqual("book", stored["assignment"]["kind"])
        # Idempotent retry: same item, no duplicated photos.
        replay = self.adopt(BATCH_A, {
            "title": "Korrigierter Titel",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": token,
        })
        retried = replay.get_json()
        self.assertTrue(retried["ok"])
        self.assertFalse(retried["saved"])
        self.assertTrue(retried["retried"])
        self.assertEqual(payload["object_id"], retried["object_id"])
        meta_after = InventoryEnrichmentStore(self.documents).object_meta(
            payload["object_id"])
        self.assertEqual(2, len(meta_after.get("photos", [])))

    def test_adopt_token_reuse_across_batches_is_rejected(self) -> None:
        from app.inventory import InventoryEnrichmentStore

        first = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        other = self.upload(PNG_RED_1PX, BATCH_B).get_json()["entry"]
        foreign_payload = _photo_payload(
            other["entry_id"], isbn=ISBN_OTHER)
        foreign_payload["prefill"] = dict(
            foreign_payload["prefill"], isbn=ISBN_OTHER,
            title="Anderes Buch")
        self.save_analyses([
            (first["entry_id"], _photo_payload(first["entry_id"])),
            (other["entry_id"], foreign_payload),
        ])
        token = self.next_token()
        created = self.adopt(BATCH_A, {
            "title": "Korrigierter Titel",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": token,
        }).get_json()
        self.assertTrue(created["saved"])
        # Same actor+token but another book group: must not attach
        # group B photos to the group A item.
        replay = self.adopt(BATCH_B, {
            "title": "Anderes Buch",
            "isbn": ISBN_OTHER,
            "item_type": "book",
            "capture_request_id": token,
        })
        self.assertEqual(400, replay.status_code)
        self.assertIn("Buchgruppe", replay.get_json()["error"])
        meta = InventoryEnrichmentStore(self.documents).object_meta(
            created["object_id"])
        self.assertEqual(1, len(meta.get("photos", [])))
        stored = CaptureInboxStore(self.documents).get(
            other["entry_id"], "anna")
        assert stored is not None
        self.assertEqual("unassigned", stored["status"])
        # Same actor+token+same batch stays idempotent.
        retried = self.adopt(BATCH_A, {
            "title": "Korrigierter Titel",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": token,
        }).get_json()
        self.assertTrue(retried["retried"])
        self.assertEqual(created["object_id"], retried["object_id"])
        # A fresh token still adopts the second group normally.
        fresh = self.adopt(BATCH_B, {
            "title": "Anderes Buch",
            "isbn": ISBN_OTHER,
            "item_type": "book",
            "capture_request_id": self.next_token(),
        }).get_json()
        self.assertTrue(fresh["saved"])
        self.assertNotEqual(created["object_id"], fresh["object_id"])

    def test_adopt_rejects_duplicates_without_silent_loss(self) -> None:
        from app.object_store import ObjectStore

        with app.app_context():
            existing = ObjectStore(self.documents).create(
                {"name": "Der Musterroman", "type": "book",
                 "identifier": ISBN_DEMO}, "anna")
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        payload = self.adopt(BATCH_A, {
            "title": "Der Musterroman",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": self.next_token(),
        }).get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["duplicate"])
        self.assertEqual(existing["object_id"], payload["object_id"])
        stored = CaptureInboxStore(self.documents).get(
            entry["entry_id"], "anna")
        assert stored is not None
        self.assertEqual("unassigned", stored["status"])
        forced = self.adopt(BATCH_A, {
            "title": "Der Musterroman",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "allow_duplicate": "1",
            "capture_request_id": self.next_token(),
        }).get_json()
        self.assertTrue(forced["saved"])

    def test_adopt_validates_input_and_token_owner(self) -> None:
        self.upload(PNG_1PX, BATCH_A)
        missing_title = self.adopt(BATCH_A, {
            "title": "",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": self.next_token(),
        })
        self.assertEqual(400, missing_title.status_code)
        self.assertIn("Buchtitel", missing_title.get_json()["error"])
        bad_isbn = self.adopt(BATCH_A, {
            "title": "Titel",
            "isbn": "9783161484101",
            "item_type": "book",
            "capture_request_id": self.next_token(),
        })
        self.assertEqual(400, bad_isbn.status_code)
        bad_token = self.adopt(BATCH_A, {
            "title": "Titel",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": "bad",
        })
        self.assertEqual(400, bad_token.status_code)
        token = self.next_token()
        created = self.adopt(BATCH_A, {
            "title": "Titel",
            "isbn": ISBN_DEMO,
            "item_type": "book",
            "capture_request_id": token,
        })
        self.assertEqual(200, created.status_code)
        self.bob.post(
            "/inventory/capture/upload",
            data={
                "photo": (io.BytesIO(PNG_RED_1PX), "foto.png"),
                "client_request_id": self.next_token(),
                "batch_id": BATCH_B,
                "_csrf_token": self.csrf(self.bob),
            },
            content_type="multipart/form-data",
        )
        foreign = self.adopt(
            BATCH_B,
            {"title": "Fremd", "item_type": "object",
             "capture_request_id": token},
            client=self.bob,
        )
        self.assertEqual(403, foreign.status_code)
        self.assertEqual(
            404, self.adopt("cc" * 16, {
                "title": "T", "item_type": "object",
                "capture_request_id": self.next_token(),
            }).status_code)
        self.assertEqual(
            400, self.adopt("bad", {
                "title": "T", "item_type": "object",
                "capture_request_id": self.next_token(),
            }).status_code)

    def test_batch_endpoints_fail_closed_on_corrupt_index(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        index_path = (Path(self.documents) / CONTROL_DIR / "inventory"
                      / "capture-inbox.json")
        broken = index_path.read_bytes() + b"\n{kaputt"
        index_path.write_bytes(broken)
        self.assertEqual(
            503, self.client.get(
                f"/inventory/capture/batch/{BATCH_A}").status_code)
        self.assertEqual(
            503, self.client.post(
                f"/inventory/capture/batch/{BATCH_A}/analyze",
                data={"_csrf_token": self.csrf()}).status_code)
        self.assertEqual(
            503, self.adopt(BATCH_A, {
                "title": "T", "item_type": "object",
                "capture_request_id": self.next_token(),
            }).status_code)
        self.assertEqual(
            503, self.client.post(
                f"/inventory/capture/{entry['entry_id']}/batch",
                json={"batch_id": BATCH_B},
                headers={"X-CSRF-Token": self.csrf()}).status_code)
        self.assertEqual(broken, index_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
