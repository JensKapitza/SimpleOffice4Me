"""Bueroengel contact suggestions: explicit Meta cloud path (demo roots only).

Covers the Meta Model API alternative to local Ollama for exactly this
endpoint: per-request consent checkbox + server flag + API key + the
pinned official endpoint/model, only on a verified synthetic demo root
(Contributor content may train future Meta models, so real contact data
never reaches this path). The single cloud call is text-only (typed
text plus local OCR text; photo bytes stay on the machine). Transport
is faked; live inference is NOT verified here. The local-path tests live
in tests/test_contextflow_contact_suggest.py and stay untouched.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from app import app
from app.contextflow.contact_suggest import suggest_contact_fields
from app.contextflow.demo import DEMO_MARKER
from app.contextflow.vision import (
    META_MODEL_API_ENDPOINT,
    META_MODEL_API_MODEL,
    MetaVisionProvider,
    VisionError,
    select_provider,
)
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password

CARD_TEXT = (
    "Max Muster\nMusterwerke GmbH\nMusterstraße 12\n12345 Berlin\n"
    "Tel: +49 30 123456\nE-Mail: max@muster.test\nwww.muster.test"
)


def _meta_settings(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "provider": "meta",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "",
        "ollama_timeout": 60,
        "meta_consent": True,
        "meta_api_key": "test-meta-key",
        "meta_endpoint": META_MODEL_API_ENDPOINT,
        "meta_model": META_MODEL_API_MODEL,
    }
    base.update(overrides)
    return base


def _demo_root(path: Path) -> Path:
    """Mark a directory as a verified synthetic demo root."""
    path.mkdir(parents=True, exist_ok=True)
    (path / DEMO_MARKER).write_text(
        json.dumps({"synthetic": True, "tool": "contextflow_demo"}),
        encoding="utf-8",
    )
    return path


def _meta_transport(payload: bytes) -> mock.MagicMock:
    """Fake the Meta Responses transport with one fixed answer."""
    response = mock.MagicMock()
    response.read.return_value = payload
    context = mock.MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    transport = mock.MagicMock()
    transport.open.return_value = context
    return transport


def _completion_payload(fields: object) -> bytes:
    return json.dumps(
        {
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"fields": fields}),
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")


class ContactMetaGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.plain = Path(self.temp.name) / "plain-root"
        self.plain.mkdir()
        self.demo = _demo_root(Path(self.temp.name) / "demo-root")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_refused_without_per_request_consent(self) -> None:
        with self.assertRaises(PermissionError):
            suggest_contact_fields(
                text="Max Muster", photos=[],
                settings=_meta_settings(), consent_meta=False, root=self.demo,
            )

    def test_refused_without_server_flag(self) -> None:
        with self.assertRaises(PermissionError):
            suggest_contact_fields(
                text="Max Muster", photos=[],
                settings=_meta_settings(meta_consent=False),
                consent_meta=True, root=self.demo,
            )

    def test_refused_without_api_key(self) -> None:
        with self.assertRaises(PermissionError):
            suggest_contact_fields(
                text="Max Muster", photos=[],
                settings=_meta_settings(meta_api_key=""),
                consent_meta=True, root=self.demo,
            )

    def test_real_roots_are_refused_even_with_consent(self) -> None:
        for root in (self.plain, None):
            with self.subTest(root="none" if root is None else "plain"):
                with (
                    self.assertRaises(PermissionError),
                    mock.patch(
                        "app.contextflow.vision.urllib.request.build_opener"
                    ) as transport,
                ):
                    suggest_contact_fields(
                        text="Max Muster", photos=[],
                        settings=_meta_settings(), consent_meta=True, root=root,
                    )
                transport.assert_not_called()

    def test_demo_barrier_holds_for_meta_everywhere(self) -> None:
        settings = _meta_settings()
        with self.assertRaises(PermissionError):
            select_provider(dict(settings), True, self.plain)
        provider = select_provider(dict(settings), True, self.demo)
        self.assertEqual("meta", provider.name)
        text_only = MetaVisionProvider(
            "k", META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL, text_only=True
        )
        with self.assertRaises(PermissionError):
            text_only.suggest(b"photo-bytes", "image/png", {})

    def test_foreign_endpoint_and_model_are_pinned(self) -> None:
        with self.assertRaises(VisionError):
            suggest_contact_fields(
                text="Max Muster", photos=[],
                settings=_meta_settings(
                    meta_endpoint="https://api.attacker.invalid/v1/chat/completions"
                ),
                consent_meta=True, root=self.demo,
            )
        with self.assertRaises(VisionError):
            suggest_contact_fields(
                text="Max Muster", photos=[],
                settings=_meta_settings(meta_model="non-meta-model"),
                consent_meta=True, root=self.demo,
            )

    def test_default_call_still_refuses_cloud(self) -> None:
        with self.assertRaises(PermissionError):
            suggest_contact_fields(
                text="Max Muster", photos=[], settings=_meta_settings(),
            )


class ContactMetaPositiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.demo = _demo_root(Path(self.temp.name) / "demo-root")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_single_text_only_call_structures_text(self) -> None:
        fields = [
            {"field": "display_name", "value": "Max Muster",
             "evidence": "Max Muster", "source": "text"},
            {"field": "email", "value": "max@muster.test",
             "evidence": "max@muster.test", "source": "text"},
        ]
        transport = _meta_transport(_completion_payload(fields))
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=transport,
        ):
            result = suggest_contact_fields(
                text=CARD_TEXT, photos=[],
                settings=_meta_settings(), consent_meta=True, root=self.demo,
            )
        self.assertEqual(2, len(result["suggestions"]))
        self.assertEqual(META_MODEL_API_MODEL, result["model"])
        self.assertIn("Muse Spark 1.3 Contributor", result["provider_label"])
        transport.open.assert_called_once()
        sent = transport.open.call_args[0][0]
        self.assertEqual(META_MODEL_API_ENDPOINT, sent.full_url)
        self.assertEqual(
            "Bearer test-meta-key", sent.get_header("Authorization")
        )
        body = json.loads(sent.data.decode("utf-8"))
        self.assertEqual(META_MODEL_API_MODEL, body["model"])
        self.assertNotIn("images", json.dumps(body))
        blocks = body["input"][0]["content"]
        self.assertEqual("input_text", blocks[0]["type"])
        self.assertIn("Max Muster", blocks[0]["text"])

    def test_photo_bytes_are_ocr_read_and_never_sent(self) -> None:
        fields = [
            {"field": "display_name", "value": "Max Muster",
             "evidence": "Max Muster", "source": "foto-1"},
        ]
        transport = _meta_transport(_completion_payload(fields))
        ocr = {"status": "completed", "text": "Max Muster", "engine": "test-ocr"}
        with mock.patch(
            "app.contextflow.contact_suggest.analyze_ocr", return_value=ocr
        ), mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=transport,
        ):
            result = suggest_contact_fields(
                text="",
                photos=[{"data": b"PHOTO-BYTES-SENTINEL", "mime": "image/png"}],
                settings=_meta_settings(), consent_meta=True, root=self.demo,
            )
        sent = transport.open.call_args[0][0]
        raw = sent.data.decode("utf-8")
        self.assertNotIn("PHOTO-BYTES-SENTINEL", raw)
        self.assertNotIn("images", raw)
        self.assertEqual("foto-1", result["sources"][0]["id"])
        self.assertEqual("test-ocr", result["sources"][0]["ocr_engine"])

    def test_transport_failure_reports_clean_error(self) -> None:
        transport = mock.MagicMock()
        transport.open.side_effect = urllib.error.URLError("meta down")
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=transport,
        ):
            with self.assertRaises(VisionError) as raised:
                suggest_contact_fields(
                    text="Max Muster", photos=[],
                    settings=_meta_settings(), consent_meta=True, root=self.demo,
                )
        self.assertIn("Meta inference failed", str(raised.exception))

    def test_malformed_answer_reports_clean_error(self) -> None:
        transport = _meta_transport(b'{"choices": []}')
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=transport,
        ):
            with self.assertRaises(VisionError) as raised:
                suggest_contact_fields(
                    text="Max Muster", photos=[],
                    settings=_meta_settings(), consent_meta=True, root=self.demo,
                )
        self.assertIn("no suggestion JSON", str(raised.exception))


class ContactMetaWebTest(unittest.TestCase):
    password = "contextflow-contact-meta-test-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "TESTING",
                "TEST_CSRF_PROTECTION", "CONTEXTFLOW_ENABLED",
                "CONTEXTFLOW_VISION_PROVIDER", "CONTEXTFLOW_OLLAMA_URL",
                "CONTEXTFLOW_OLLAMA_MODEL", "CONTEXTFLOW_OLLAMA_TIMEOUT",
                "CONTEXTFLOW_META_CONSENT", "CONTEXTFLOW_META_ENDPOINT",
                "CONTEXTFLOW_META_MODEL",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="meta",
            CONTEXTFLOW_OLLAMA_URL="http://127.0.0.1:11434",
            CONTEXTFLOW_OLLAMA_MODEL="",
            CONTEXTFLOW_OLLAMA_TIMEOUT=60,
            CONTEXTFLOW_META_CONSENT=True,
            CONTEXTFLOW_META_ENDPOINT=META_MODEL_API_ENDPOINT,
            CONTEXTFLOW_META_MODEL=META_MODEL_API_MODEL,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.client = app.test_client()
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password,
                  "_csrf_token": self.csrf(self.client, "/auth/login")},
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/contextflow/") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def test_cloud_without_consent_checkbox_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "k"}):
            response = self.client.post(
                "/contextflow/contact-suggest",
                data={
                    "_csrf_token": self.csrf(self.client),
                    "contact_text": "Max Muster",
                },
            )
        self.assertEqual(403, response.status_code)

    def test_cloud_with_consent_refused_outside_demo_root(self) -> None:
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "k"}), mock.patch(
            "app.contextflow.vision.urllib.request.build_opener"
        ) as transport:
            response = self.client.post(
                "/contextflow/contact-suggest",
                data={
                    "_csrf_token": self.csrf(self.client),
                    "contact_text": "Max Muster",
                    "consent_meta": "yes",
                },
            )
        self.assertEqual(403, response.status_code)
        transport.assert_not_called()

    def test_cloud_with_consent_answers_on_demo_root(self) -> None:
        _demo_root(Path(self.temp.name) / "documents")
        fields = [
            {"field": "display_name", "value": "Max Muster",
             "evidence": "Max Muster", "source": "text"},
        ]
        transport = _meta_transport(_completion_payload(fields))
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "k"}), mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=transport,
        ):
            response = self.client.post(
                "/contextflow/contact-suggest",
                data={
                    "_csrf_token": self.csrf(self.client),
                    "contact_text": "Max Muster",
                    "consent_meta": "yes",
                },
            )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(1, len(payload["suggestions"]))
        self.assertEqual(META_MODEL_API_MODEL, payload["model"])

    def test_contacts_page_offers_explicit_cloud_choice(self) -> None:
        body = self.client.get("/documents/contacts").get_data(as_text=True)
        self.assertIn('id="engel-contact-consent"', body)
        self.assertIn('name="consent_meta"', body)
        self.assertIn("Muse Spark 1.3 Contributor", body)
        self.assertIn(
            "eingegebener Text und lokal erkannter Foto-Text gehen an Meta; "
            "Bilddateien bleiben lokal",
            body,
        )
        self.assertIn(
            "Contributor-Modus: Inhalte können zum Training künftiger "
            "Meta-Modelle verwendet werden.",
            body,
        )
        self.assertIn("Nur synthetische Demo-Daten", body)
        match = re.search(
            r'<input[^>]*id="engel-contact-consent"[^>]*>', body
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertNotIn("checked", match.group(0))


if __name__ == "__main__":
    unittest.main()
