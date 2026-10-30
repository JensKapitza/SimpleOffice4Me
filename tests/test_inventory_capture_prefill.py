"""Editable description/tags prefill across the persistent capture inbox.

``capture_ml_label`` keeps the validated note/tags, ``analyze_photo``
exposes them as editable prefill (objects and books), batch review
merges them conservatively (first note wins, tags unioned capped),
and both single and batch adoption send the reviewed values through
the existing form POSTs. Nothing is ever saved without an explicit
adopt click; prices and book-data flows stay untouched.
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
from app.capture_inbox_store import CaptureInboxStore
from app.db import ensure_auth_database, get_db
from app.object_store import ObjectStore
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX

BATCH_D = "dd" * 16
ISBN_DEMO = "9783161484100"


def _photo_payload(photo_id: str, **overrides) -> dict:
    payload = {
        "ok": True,
        "photo_id": photo_id,
        "sha256": "0" * 64,
        "kind": "object",
        "kind_reason": "Objekt erkannt: Hammer",
        "needs_name": False,
        "isbn": "",
        "prefill": {
            "isbn": "",
            "description": "",
            "tags": [],
            "name": "Hammer",
        },
        "ocr": {"engine": "rapidocr", "status": "no-text",
                "characters": 0, "confidence": None, "error": ""},
        "ml": {"available": True, "label": "Hammer", "item_type": "tool",
               "provider": "local", "provider_label": "Ollama (lokal)",
               "model": "llama3.2-vision", "error": ""},
        "duplicates": [],
        "missing_fields": [],
    }
    payload.update(overrides)
    return payload


def _analyze(raw: bytes, ml: dict, ocr_text: str = "") -> dict:
    from app.inventory_capture import analyze_photo

    ocr = {"text": ocr_text, "engine": "rapidocr", "status": "completed",
           "characters": len(ocr_text), "confidence": 90, "error": ""}
    with (
        mock.patch("app.library.ocr_input.optimize_upload",
                   return_value=(b"fake-png", {})),
        mock.patch("app.inventory_capture.capture_ocr", return_value=ocr),
        mock.patch("app.inventory_capture.capture_ml_label",
                   return_value=ml),
        mock.patch("app.inventory_capture.capture_duplicates",
                   return_value=[]),
        mock.patch("app.inventory_capture.capture_metadata",
                   return_value={}),
    ):
        return analyze_photo(raw, "photo-1")


class AnalyzePrefillTests(unittest.TestCase):
    def test_object_prefill_carries_note_and_tags(self) -> None:
        ml = {"available": True, "label": "Hammer", "item_type": "tool",
              "isbn": "", "note": "Roter Schlosserhammer.",
              "tags": ["rot", "Metall"],
              "provider": "local", "provider_label": "Ollama (lokal)",
              "model": "llama3.2-vision", "error": ""}
        payload = _analyze(b"raw", ml)
        self.assertEqual("object", payload["kind"])
        self.assertEqual("Hammer", payload["prefill"]["name"])
        self.assertEqual("Roter Schlosserhammer.",
                         payload["prefill"]["description"])
        self.assertEqual(["rot", "Metall"], payload["prefill"]["tags"])
        self.assertEqual([], payload["missing_fields"])

    def test_book_flow_gains_prefill_without_losing_fields(self) -> None:
        ml = {"available": True, "label": "Buch", "item_type": "book",
              "isbn": ISBN_DEMO, "note": "Blaues Taschenbuch.",
              "tags": ["blau", "Taschenbuch"],
              "provider": "local", "provider_label": "Ollama (lokal)",
              "model": "llama3.2-vision", "error": ""}
        payload = _analyze(b"raw", ml)
        self.assertEqual("book", payload["kind"])
        self.assertEqual(ISBN_DEMO, payload["isbn"])
        self.assertEqual("Blaues Taschenbuch.",
                         payload["prefill"]["description"])
        self.assertEqual(["blau", "Taschenbuch"], payload["prefill"]["tags"])
        self.assertIn("title", payload["missing_fields"])

    def test_legacy_ml_result_without_note_or_tags_stays_empty(self) -> None:
        ml = {"available": True, "label": "Hammer", "item_type": "tool",
              "isbn": "", "provider": "local",
              "provider_label": "Ollama (lokal)",
              "model": "llama3.2-vision", "error": ""}
        payload = _analyze(b"raw", ml)
        self.assertEqual("", payload["prefill"]["description"])
        self.assertEqual([], payload["prefill"]["tags"])

    def test_non_list_tags_are_dropped_defensively(self) -> None:
        ml = {"available": True, "label": "Hammer", "item_type": "tool",
              "isbn": "", "note": "n", "tags": "rot, Metall",
              "provider": "local", "provider_label": "x",
              "model": "m", "error": ""}
        payload = _analyze(b"raw", ml)
        self.assertEqual([], payload["prefill"]["tags"])

    def test_ml_label_keeps_validated_note_and_tags(self) -> None:
        from app.inventory_capture import capture_ml_label

        answer = {
            "provider": "local", "provider_label": "Ollama (lokal)",
            "model": "llama3.2-vision",
            "suggestion": {"object_name": "Hammer", "item_type": "tool",
                           "confidence": 80, "note": "n",
                           "isbn": "", "tags": ["rot", 7, ""]},
        }
        with mock.patch(
            "app.contextflow.vision.suggest_inventory_object",
            return_value=answer,
        ):
            result = capture_ml_label(b"fake-png-bytes", "")
        self.assertTrue(result["available"])
        self.assertEqual("n", result["note"])
        self.assertEqual(["rot", ""], result["tags"])

    def test_ml_label_unavailable_keeps_empty_note_and_tags(self) -> None:
        from app.contextflow.vision import MockBlockedError
        from app.inventory_capture import capture_ml_label

        with mock.patch(
            "app.contextflow.vision.suggest_inventory_object",
            side_effect=MockBlockedError("kein Meta-Modell verfügbar"),
        ):
            result = capture_ml_label(b"fake-png-bytes", "")
        self.assertFalse(result["available"])
        self.assertEqual("", result["note"])
        self.assertEqual([], result["tags"])


class CombinePrefillTests(unittest.TestCase):
    def test_first_note_wins_and_tags_union_capped(self) -> None:
        first = _photo_payload("photo-1")
        first["prefill"]["description"] = "Erste Notiz."
        first["prefill"]["tags"] = ["rot", "Metall"]
        second = _photo_payload("photo-2")
        second["prefill"]["description"] = "Zweite Notiz."
        second["prefill"]["tags"] = ["METALL", "Holz", "Werkstatt"]
        combined = combine_batch_analyses([first, second])
        self.assertEqual("Erste Notiz.", combined["prefill"]["description"])
        self.assertEqual(["rot", "Metall", "Holz", "Werkstatt"],
                         combined["prefill"]["tags"])
        self.assertEqual("Erste Notiz.", combined["photos"][0]["ml_note"])
        self.assertEqual(["rot", "Metall"],
                         combined["photos"][0]["ml_tags"])
        self.assertEqual("Zweite Notiz.", combined["photos"][1]["ml_note"])

    def test_tag_union_is_capped(self) -> None:
        first = _photo_payload("photo-1")
        first["prefill"]["tags"] = [f"tag-{index:02d}" for index in range(10)]
        second = _photo_payload("photo-2")
        second["prefill"]["tags"] = [f"tag-{index:02d}" for index in range(8, 16)]
        combined = combine_batch_analyses([first, second])
        self.assertEqual(12, len(combined["prefill"]["tags"]))
        self.assertEqual("tag-00", combined["prefill"]["tags"][0])

    def test_legacy_rows_without_prefill_keys_merge_empty(self) -> None:
        first = _photo_payload("photo-1")
        del first["prefill"]["description"]
        del first["prefill"]["tags"]
        combined = combine_batch_analyses([first, {"ok": False}])
        self.assertEqual("", combined["prefill"]["description"])
        self.assertEqual([], combined["prefill"]["tags"])
        self.assertEqual("", combined["photos"][0]["ml_note"])
        self.assertEqual([], combined["photos"][0]["ml_tags"])

    def test_book_merge_keeps_book_flow_and_adds_prefill(self) -> None:
        first = _photo_payload("photo-1", kind="book", isbn=ISBN_DEMO)
        first["prefill"] = {"isbn": ISBN_DEMO, "title": "Titel",
                            "authors": ["Autor"], "year": "2021",
                            "publisher": "Verlag", "title_lines": [],
                            "description": "Klappentext-Hinweis.",
                            "tags": ["blau"]}
        combined = combine_batch_analyses([first])
        self.assertEqual("book", combined["kind"])
        self.assertEqual("Titel", combined["prefill"]["title"])
        self.assertEqual(["Autor"], combined["prefill"]["authors"])
        self.assertEqual("Klappentext-Hinweis.",
                         combined["prefill"]["description"])
        self.assertEqual(["blau"], combined["prefill"]["tags"])


class AdoptPrefillHttpTests(unittest.TestCase):
    keys = ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
    password = "anna-test-pw-1"  # noqa: S105 (synthetic test credential)

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
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("anna", hash_password(self.password), "Anna", 1),
            )
            db.commit()
        self.client = self.login("anna")
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
            data={"username": username, "password": self.password,
                  "_csrf_token": match.group(1).decode()},
        )
        return client

    def csrf(self) -> str:
        page = self.client.get("/inventory/capture")
        match = re.search(rb'data-csrf="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1).decode()

    def next_token(self) -> str:
        self.token_counter += 1
        return f"{self.token_counter:032x}"[-32:]

    def upload(self, data: bytes, batch_id: str):
        form = {"photo": (io.BytesIO(data), "foto.png"),
                "client_request_id": self.next_token(),
                "_csrf_token": self.csrf(),
                "batch_id": batch_id}
        return self.client.post(
            "/inventory/capture/upload", data=form,
            content_type="multipart/form-data")

    def test_review_shows_merged_prefill_and_adopt_stores_it(self) -> None:
        first = self.upload(PNG_1PX, BATCH_D).get_json()["entry"]
        second = self.upload(PNG_1PX + b"\x01second", BATCH_D).get_json()["entry"]
        store = CaptureInboxStore(self.documents)
        payload_one = _photo_payload(first["entry_id"])
        payload_one["prefill"]["description"] = "Roter Hammer."
        payload_one["prefill"]["tags"] = ["rot"]
        payload_two = _photo_payload(second["entry_id"])
        payload_two["prefill"]["description"] = "Zweite Sicht."
        payload_two["prefill"]["tags"] = ["Metall", "rot"]
        self.assertIsNotNone(
            store.save_analysis(first["entry_id"], "anna", payload_one))
        self.assertIsNotNone(
            store.save_analysis(second["entry_id"], "anna", payload_two))
        review = self.client.get(f"/inventory/capture/batch/{BATCH_D}")
        self.assertEqual(200, review.status_code)
        combined = review.get_json()["combined"]
        self.assertEqual("Roter Hammer.", combined["prefill"]["description"])
        self.assertEqual(["rot", "Metall"], combined["prefill"]["tags"])
        objects_before = ObjectStore(self.documents).objects()
        self.assertEqual([], [row for row in objects_before
                              if row.get("name") == "Hammer"])
        form = {"title": "Hammer", "item_type": "object",
                "description": "Geprüfte Beschreibung.",
                "tags": "werkstatt, rot",
                "capture_request_id": self.next_token(),
                "_csrf_token": self.csrf()}
        adopted = self.client.post(
            f"/inventory/capture/batch/{BATCH_D}/adopt", data=form,
            content_type="multipart/form-data")
        self.assertEqual(200, adopted.status_code)
        result = adopted.get_json()
        self.assertTrue(result["ok"])
        item = ObjectStore(self.documents).object(result["object_id"])
        self.assertEqual("Geprüfte Beschreibung.", item["description"])
        stored_tags = set(item["tags"])
        self.assertIn("Inventar", stored_tags)
        self.assertIn("werkstatt", stored_tags)
        self.assertIn("rot", stored_tags)

    def test_adopt_tags_are_validated_and_capped(self) -> None:
        first = self.upload(PNG_1PX, BATCH_D).get_json()["entry"]
        store = CaptureInboxStore(self.documents)
        self.assertIsNotNone(store.save_analysis(
            first["entry_id"], "anna", _photo_payload(first["entry_id"])))
        form = {"title": "Hammer", "item_type": "object",
                "description": "d", "tags": "  , ok," + "x" * 600,
                "capture_request_id": self.next_token(),
                "_csrf_token": self.csrf()}
        adopted = self.client.post(
            f"/inventory/capture/batch/{BATCH_D}/adopt", data=form,
            content_type="multipart/form-data")
        self.assertEqual(200, adopted.status_code)
        item = ObjectStore(self.documents).object(
            adopted.get_json()["object_id"])
        stored_tags = set(item["tags"])
        self.assertIn("Inventar", stored_tags)
        self.assertIn("ok", stored_tags)
        joined = ",".join(sorted(stored_tags, key=str.casefold))
        self.assertLessEqual(len(joined), 520)


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "static" / "js" / "capture_inbox.js").read_text(
        encoding="utf-8")


def _region(source: str, start: str, end: str) -> str:
    head = source.index(start)
    tail = source.index(end, head + len(start))
    return source[head:tail]


class CapturePrefillStaticTests(unittest.TestCase):
    def test_single_review_has_editable_prefill_fields(self) -> None:
        body = _region(
            _source(),
            "if (analysis.kind === 'book') {",
            "const missing = (analysis.missing_fields || []).join(', ');",
        )
        self.assertIn("addField('Beschreibung', 'description', prefill.description)", body)
        self.assertIn("addField('Tags (Komma-getrennt)', 'tags'", body)
        self.assertIn("prefill.tags.join(', ')", body)

    def test_single_adopt_sends_reviewed_fields(self) -> None:
        body = _region(
            _source(),
            "const adoptDraft = async (entry, analysis, inputs, button, state) => {",
            "const linkDuplicate = async (",
        )
        self.assertIn("data.append('description', get('description'));", body)
        self.assertIn("data.append('tags', get('tags'));", body)
        self.assertNotIn("submit(", body)
        self.assertNotIn("requestSubmit", body)

    def test_batch_review_has_editable_prefill_fields(self) -> None:
        body = _region(
            _source(),
            "if (combined.kind === 'book') {",
            "const missing = (combined.missing_fields || []).join(', ');",
        )
        self.assertIn("addField('Beschreibung', 'description', prefill.description)", body)
        self.assertIn("addField('Tags (Komma-getrennt)', 'tags'", body)

    def test_batch_adopt_sends_reviewed_fields(self) -> None:
        body = _region(
            _source(),
            "adopt.addEventListener('click', async () => {",
            "actions.appendChild(adopt);",
        )
        self.assertIn("data.append('description', get('description'));", body)
        self.assertIn("data.append('tags', get('tags'));", body)
        self.assertIn("/adopt", body)
        self.assertNotIn("submit(", body)
        self.assertNotIn("requestSubmit", body)

    def test_batch_photo_lines_show_note_and_tags(self) -> None:
        source = _source()
        self.assertIn("photo.ml_note", source)
        self.assertIn("photo.ml_tags", source)
        self.assertIn("Notiz:", source)

    def test_no_autosave_wiring(self) -> None:
        source = _source()
        self.assertNotIn(".submit(", source)
        self.assertNotIn("requestSubmit", source)
        self.assertIn("adopt.type = 'button';", source)


if __name__ == "__main__":
    unittest.main()
