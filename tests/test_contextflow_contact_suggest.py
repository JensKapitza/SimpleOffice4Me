"""Bueroengel contact suggestions: text + photos become grounded field drafts.

Covers prompt building, evidence-grounded validation (unsupported fields
are dropped, typo fixes are marked), provider gates (local Meta only,
mock refused, cloud refused), the single text-only model call (photo
bytes never leave the machine), the /contact-suggest route, the Engel
card markup on the contact form, and the manual address save path.
Transport and OCR are faked; live inference is NOT verified here.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from app import app
from app.contact_store import ContactStore
from app.contextflow.contact_suggest import (
    MAX_PHOTOS,
    build_contact_prompt,
    suggest_contact_fields,
    validate_contact_suggestions,
)
from app.contextflow.vision import (
    InferenceUnavailable,
    MockBlockedError,
    VisionError,
)
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from tests.contextflow_ollama_fakes import (
    SHOW_PLAIN,
    _chat_bodies,
    _local_route,
)

CARD_TEXT = (
    "Max Muster\nMusterwerke GmbH\nMusterstraße 12\n12345 Berlin\n"
    "Tel: +49 30 123456\nE-Mail: max@muster.test\nwww.muster.test"
)
TEXT_SOURCE = [{"id": "text", "label": "Eingefügter Text", "text": CARD_TEXT}]


def _png_bytes(size: int = 8) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (size, size), color=(20, 60, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


def _chat_chunks(payload: object) -> list[dict[str, object]]:
    body = json.dumps(payload, ensure_ascii=False)
    half = len(body) // 2
    return [
        {"message": {"content": body[:half]}},
        {"message": {"content": body[half:]}, "done": True},
    ]


def _settings(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "provider": "local",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "llama3.2:1b",
        "ollama_timeout": 60,
        "meta_consent": False,
        "meta_api_key": "",
        "meta_endpoint": "",
        "meta_model": "",
    }
    base.update(overrides)
    return base


class ContactPromptTest(unittest.TestCase):
    def test_prompt_labels_sources_and_requires_quoted_json(self) -> None:
        prompt = build_contact_prompt(
            [
                {"id": "text", "label": "Eingefügter Text", "text": "Max Muster"},
                {"id": "foto-1", "label": "Foto 1", "text": "Musterstraße 12"},
            ]
        )
        self.assertIn("[text] Eingefügter Text", prompt)
        self.assertIn("[foto-1] Foto 1", prompt)
        self.assertIn("Max Muster", prompt)
        self.assertIn("display_name", prompt)
        self.assertIn("postal", prompt)
        self.assertIn("evidence", prompt)
        self.assertIn("ohne Beleg kein Feld", prompt)


class ContactValidationTest(unittest.TestCase):
    def test_grounded_fields_pass_and_typo_fix_is_marked(self) -> None:
        sources = [
            {"id": "text", "label": "Eingefügter Text", "text": "Max Musteer\n12345 Berlin"}
        ]
        raw = {
            "fields": [
                {"field": "display_name", "value": "Max Muster",
                 "evidence": "Max Musteer", "source": "text"},
                {"field": "postal", "value": "12345",
                 "evidence": "12345", "source": "text"},
                {"field": "city", "value": "Berlin",
                 "evidence": "Berlin", "source": "text"},
            ]
        }
        suggestions, dropped = validate_contact_suggestions(raw, sources)
        self.assertEqual(0, dropped)
        self.assertEqual(3, len(suggestions))
        by_field = {item["field"]: item for item in suggestions}
        self.assertTrue(by_field["display_name"]["corrected"])
        self.assertFalse(by_field["postal"]["corrected"])
        self.assertEqual("Anzeigename", by_field["display_name"]["label"])
        self.assertEqual("Max Musteer", by_field["display_name"]["evidence"])
        self.assertEqual("text", by_field["display_name"]["source"])

    def test_unsupported_entries_are_dropped_and_counted(self) -> None:
        raw = {
            "fields": [
                {"field": "display_name", "value": "Max Muster",
                 "evidence": "Max Muster", "source": "text"},
                {"field": "birthday", "value": "2000-01-01",
                 "evidence": "2000", "source": "text"},
                {"field": "note", "value": "x" * 10,
                 "evidence": "Max Muster", "source": "text"},
                {"field": "company", "value": "C" * 301,
                 "evidence": "Musterwerke GmbH", "source": "text"},
                {"field": "email", "value": "max@muster.test",
                 "evidence": "", "source": "text"},
                {"field": "company", "value": "Erfunden AG",
                 "evidence": "Erfunden AG", "source": "text"},
                {"field": "city", "value": "Hamburg",
                 "evidence": "Berlin", "source": "foto-9"},
                {"field": "email", "value": "keine-mail",
                 "evidence": "max@muster.test", "source": "text"},
                {"field": "phone", "value": "123",
                 "evidence": "+49 30 123456", "source": "text"},
                {"field": "country", "value": "Deutschland",
                 "evidence": "Berlin", "source": "text"},
                {"field": "display_name", "value": "Max Muster",
                 "evidence": "Max Muster", "source": "text"},
                "not-a-dict",
            ]
        }
        suggestions, dropped = validate_contact_suggestions(raw, TEXT_SOURCE)
        self.assertEqual(1, len(suggestions))
        self.assertEqual("display_name", suggestions[0]["field"])
        self.assertEqual(11, dropped)

    def test_malformed_payload_raises(self) -> None:
        for raw in ([], {}, {"fields": {}}, {"fields": None}, "x"):
            with self.subTest(raw=raw):
                with self.assertRaises(VisionError):
                    validate_contact_suggestions(raw, TEXT_SOURCE)

    def test_valid_empty_returns_empty_list(self) -> None:
        suggestions, dropped = validate_contact_suggestions({"fields": []}, TEXT_SOURCE)
        self.assertEqual([], suggestions)
        self.assertEqual(0, dropped)


class ContactBackendTest(unittest.TestCase):
    def test_too_many_photos_rejected(self) -> None:
        photos = [{"data": b"x", "mime": "image/png"}] * (MAX_PHOTOS + 1)
        with self.assertRaises(ValueError) as raised:
            suggest_contact_fields(text="", photos=photos, settings=_settings())
        self.assertIn(str(MAX_PHOTOS), str(raised.exception))

    def test_missing_sources_rejected(self) -> None:
        with self.assertRaises(ValueError) as raised:
            suggest_contact_fields(text="  ", photos=[], settings=_settings())
        self.assertIn("Kontakttext", str(raised.exception))

    def test_unreadable_photos_without_text_rejected(self) -> None:
        with mock.patch(
            "app.contextflow.contact_suggest.analyze_ocr",
            return_value={"status": "error", "error": "kaputt"},
        ):
            with self.assertRaises(ValueError) as raised:
                suggest_contact_fields(
                    text="",
                    photos=[{"data": b"x", "mime": "image/png"}],
                    settings=_settings(),
                )
        self.assertIn("lesbares Foto", str(raised.exception))

    def test_empty_model_mock_and_cloud_are_refused(self) -> None:
        with self.assertRaises(InferenceUnavailable):
            suggest_contact_fields(
                text="Max Muster", photos=[], settings=_settings(ollama_model="")
            )
        with self.assertRaises(MockBlockedError):
            suggest_contact_fields(
                text="Max Muster", photos=[], settings=_settings(provider="mock")
            )
        with self.assertRaises(PermissionError):
            suggest_contact_fields(
                text="Max Muster",
                photos=[],
                settings=_settings(provider="meta", meta_consent=True,
                                   meta_api_key="k", meta_endpoint="e", meta_model="m"),
            )

    def test_single_text_only_call_structures_text(self) -> None:
        reply = {
            "fields": [
                {"field": "display_name", "value": "Max Muster",
                 "evidence": "Max Muster", "source": "text"},
                {"field": "email", "value": "max@muster.test",
                 "evidence": "max@muster.test", "source": "text"},
            ]
        }
        calls: list[str] = []
        route = _local_route(show=SHOW_PLAIN, chat=_chat_chunks(reply), calls=calls)
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            result = suggest_contact_fields(
                text=CARD_TEXT, photos=[], settings=_settings()
            )
        bodies = _chat_bodies(opener)
        self.assertEqual(1, len(bodies))
        self.assertEqual(1, calls.count("/api/chat"))
        self.assertNotIn("images", json.dumps(bodies[0]))
        self.assertIn("Max Muster", bodies[0]["messages"][0]["content"])
        self.assertEqual(2, len(result["suggestions"]))
        self.assertEqual("llama3.2:1b", result["model"])
        self.assertIn("Ollama", result["provider_label"])
        self.assertEqual("text", result["sources"][0]["id"])
        self.assertIn("Max Muster", result["sources"][0]["excerpt"])

    def test_photo_bytes_are_ocr_read_and_never_sent(self) -> None:
        reply = {
            "fields": [
                {"field": "display_name", "value": "Max Muster",
                 "evidence": "Max Muster", "source": "foto-1"},
            ]
        }
        route = _local_route(show=SHOW_PLAIN, chat=_chat_chunks(reply))
        ocr = {"status": "completed", "text": "Max Muster", "engine": "test-ocr"}
        with mock.patch(
            "app.contextflow.contact_suggest.analyze_ocr", return_value=ocr
        ) as ocr_call, mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            result = suggest_contact_fields(
                text="",
                photos=[{"data": b"PHOTO-BYTES-SENTINEL", "mime": "image/png"}],
                settings=_settings(),
            )
        ocr_call.assert_called_once()
        bodies = _chat_bodies(opener)
        self.assertEqual(1, len(bodies))
        self.assertNotIn("PHOTO-BYTES-SENTINEL", json.dumps(bodies[0]))
        self.assertNotIn("images", json.dumps(bodies[0]))
        self.assertEqual("foto-1", result["sources"][0]["id"])
        self.assertEqual("test-ocr", result["sources"][0]["ocr_engine"])

    def test_failing_photo_warns_but_text_still_structured(self) -> None:
        reply = {"fields": []}
        route = _local_route(show=SHOW_PLAIN, chat=_chat_chunks(reply))
        with mock.patch(
            "app.contextflow.contact_suggest.analyze_ocr",
            return_value={"status": "completed", "text": "", "engine": "test-ocr"},
        ), mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ):
            result = suggest_contact_fields(
                text="Max Muster",
                photos=[{"data": b"x", "mime": "image/png"}],
                settings=_settings(),
            )
        self.assertEqual([], result["suggestions"])
        joined = " ".join(result["warnings"])
        self.assertIn("Foto 1", joined)
        self.assertIn("Keine belegbaren", joined)


class ContactSuggestWebTest(unittest.TestCase):
    password = "contextflow-contact-suggest-test-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "TESTING",
                "TEST_CSRF_PROTECTION", "CONTEXTFLOW_ENABLED",
                "CONTEXTFLOW_VISION_PROVIDER", "CONTEXTFLOW_OLLAMA_URL",
                "CONTEXTFLOW_OLLAMA_MODEL", "CONTEXTFLOW_OLLAMA_TIMEOUT",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="local",
            CONTEXTFLOW_OLLAMA_URL="http://127.0.0.1:11434",
            CONTEXTFLOW_OLLAMA_MODEL="llama3.2:1b",
            CONTEXTFLOW_OLLAMA_TIMEOUT=60,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            for username, admin in (("admin", 1), ("berta", 0)):
                db.execute(
                    "INSERT INTO user(username,password,display_name,is_admin,"
                    "is_disabled,auth_version,created_at,updated_at)"
                    " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, hash_password(self.password), username.title(), admin),
                )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.admin = self.login("admin")
        self.berta = self.login("berta")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/contextflow/") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def login(self, username: str):
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"username": username, "password": self.password,
                  "_csrf_token": self.csrf(client, "/auth/login")},
        )
        return client

    def test_flag_off_answers_404(self) -> None:
        token = self.csrf(self.admin)
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            response = self.admin.post(
                "/contextflow/contact-suggest", data={"_csrf_token": token}
            )
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertEqual(404, response.status_code)

    def test_missing_csrf_and_revoked_feature_are_refused(self) -> None:
        self.assertEqual(
            403,
            self.admin.post(
                "/contextflow/contact-suggest", data={"contact_text": "Max Muster"}
            ).status_code,
        )
        with app.app_context():
            db = get_db()
            berta_id = db.execute(
                "SELECT id FROM user WHERE username = 'berta'"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO user_permission(user_id, feature, enabled, updated_at)"
                " VALUES (?, 'documents', 0, CURRENT_TIMESTAMP)",
                (berta_id,),
            )
            db.commit()
        token = self.csrf(self.berta)
        self.assertEqual(
            403,
            self.berta.post(
                "/contextflow/contact-suggest",
                data={"_csrf_token": token, "contact_text": "Max Muster"},
            ).status_code,
        )

    def test_empty_request_answers_400(self) -> None:
        response = self.admin.post(
            "/contextflow/contact-suggest",
            data={"_csrf_token": self.csrf(self.admin)},
        )
        self.assertEqual(400, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("Kontakttext", payload["error"])

    def test_too_many_photos_and_bad_photo_answer_400(self) -> None:
        token = self.csrf(self.admin)
        many = {
            "_csrf_token": token,
            "photos": [
                (BytesIO(_png_bytes()), f"{index}.png", "image/png")
                for index in range(MAX_PHOTOS + 1)
            ],
        }
        response = self.admin.post("/contextflow/contact-suggest", data=many)
        self.assertEqual(400, response.status_code)
        self.assertIn(str(MAX_PHOTOS), response.get_json()["error"])

        bad = {
            "_csrf_token": token,
            "photos": (BytesIO(b"not-an-image"), "x.txt", "text/plain"),
        }
        response = self.admin.post("/contextflow/contact-suggest", data=bad)
        self.assertEqual(400, response.status_code)
        self.assertIn("Foto 1", response.get_json()["error"])

    def test_mock_provider_answers_503(self) -> None:
        app.config.update(CONTEXTFLOW_VISION_PROVIDER="mock")
        try:
            response = self.admin.post(
                "/contextflow/contact-suggest",
                data={
                    "_csrf_token": self.csrf(self.admin),
                    "contact_text": "Max Muster",
                },
            )
        finally:
            app.config.update(CONTEXTFLOW_VISION_PROVIDER="local")
        self.assertEqual(503, response.status_code)
        self.assertFalse(response.get_json()["ok"])

    def test_happy_path_returns_suggestions_and_stores_nothing(self) -> None:
        reply = {
            "fields": [
                {"field": "display_name", "value": "Max Muster",
                 "evidence": "Max Muster", "source": "text"},
                {"field": "street", "value": "Musterstraße 12",
                 "evidence": "Musterstraße 12", "source": "text"},
            ]
        }
        route = _local_route(show=SHOW_PLAIN, chat=_chat_chunks(reply))
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            response = self.admin.post(
                "/contextflow/contact-suggest",
                data={
                    "_csrf_token": self.csrf(self.admin),
                    "contact_text": CARD_TEXT,
                },
            )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(2, len(payload["suggestions"]))
        self.assertEqual("llama3.2:1b", payload["model"])
        self.assertIn("Ollama", payload["provider_label"])
        self.assertEqual(1, len(_chat_bodies(opener)))
        self.assertEqual([], ContactStore(self.root).contacts("admin"))
        contacts_file = self.root / ".simpleoffice-meta" / "contacts.json"
        if contacts_file.exists():
            stored = json.loads(contacts_file.read_text(encoding="utf-8"))
            self.assertEqual([], stored.get("contacts", []))

    def test_contacts_page_shows_engel_card_only_when_enabled(self) -> None:
        body = self.admin.get("/documents/contacts").get_data(as_text=True)
        self.assertIn('id="engel-contact-form"', body)
        self.assertIn('id="engel-contact-card"', body)
        self.assertIn('data-suggest-url="/contextflow/contact-suggest"', body)
        self.assertIn('name="contact_address_street"', body)
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            body = self.admin.get("/documents/contacts").get_data(as_text=True)
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertNotIn('id="engel-contact-form"', body)
        self.assertIn('name="contact_address_street"', body)


class ContactManualAddressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "jens", "password": "sicheres-passwort"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "jens", "password": "sicheres-passwort"},
        )
        self.store = ContactStore(self.root)

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _saved(self, name: str):
        return next(
            (
                item
                for item in self.store.contacts("jens")
                if item["fields"]["display_name"] == name
            ),
            None,
        )

    def test_create_with_address_stores_labeled_components(self):
        response = self.client.post("/documents/contacts", data={
            "display_name": "Anna Beispiel",
            "contact_address_label": "Geschäftlich",
            "contact_address_street": "Musterstraße 12",
            "contact_address_postal": "12345",
            "contact_address_city": "Berlin",
            "contact_address_country": "DE",
        })
        self.assertEqual(302, response.status_code)
        saved = self._saved("Anna Beispiel")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(1, len(saved["addresses"]))
        address = saved["addresses"][0]
        self.assertEqual("Geschäftlich", address["label"])
        self.assertEqual("Musterstraße 12", address["components"]["street"])
        self.assertEqual("12345", address["components"]["postal"])
        self.assertEqual("Berlin", address["components"]["city"])
        self.assertEqual("DE", address["components"]["country"])
        self.assertIn("Musterstraße 12", address["value"])

    def test_create_without_address_stores_no_address(self):
        response = self.client.post("/documents/contacts", data={
            "display_name": "Ohne Anschrift",
        })
        self.assertEqual(302, response.status_code)
        saved = self._saved("Ohne Anschrift")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual([], saved["addresses"])

    def test_unknown_label_falls_back_to_privat(self):
        self.client.post("/documents/contacts", data={
            "display_name": "Label Fallback",
            "contact_address_label": "Erfunden",
            "contact_address_city": "Berlin",
        })
        saved = self._saved("Label Fallback")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual("Privat", saved["addresses"][0]["label"])

    def test_invalid_address_is_rejected_without_contact(self):
        response = self.client.post("/documents/contacts", data={
            "display_name": "Ungültige Anschrift",
            "contact_address_country": "DEU",
            "contact_address_city": "Berlin",
        })
        self.assertEqual(302, response.status_code)
        self.assertIsNone(self._saved("Ungültige Anschrift"))

        response = self.client.post("/documents/contacts", data={
            "display_name": "Zu lange Straße",
            "contact_address_street": "S" * 301,
        })
        self.assertEqual(302, response.status_code)
        self.assertIsNone(self._saved("Zu lange Straße"))

    def test_edit_ignores_manual_address_fields(self):
        created = self.store.upsert({"display_name": "Bestehend"}, "jens")
        response = self.client.post("/documents/contacts", data={
            "contact_id": created["contact_id"],
            "display_name": "Bestehend",
            "contact_address_city": "Hamburg",
        })
        self.assertEqual(302, response.status_code)
        saved = self.store.get(created["contact_id"], "jens")
        self.assertEqual([], saved["addresses"])


if __name__ == "__main__":
    unittest.main()
