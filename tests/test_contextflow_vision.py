"""ContextFlow vision slice: mock-provider draft tests (no network).

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path. Nothing here installs, pulls or calls model backends; the local and
Meta providers are covered only for validation, gating and clean failure.
Live inference is NOT verified (no Ollama, no MODEL_API_KEY here).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from app import app
from app.contextflow.actions import ApprovalStale
from app.contextflow.demo import DEMO_MARKER
from app.contextflow.vision import (
    META_MODEL_API_ENDPOINT,
    META_MODEL_API_MODEL,
    OLLAMA_NUM_PREDICT,
    InferenceTimeoutError,
    InferenceUnavailable,
    LocalVisionProvider,
    MetaVisionProvider,
    MockBlockedError,
    MockVisionProvider,
    VisionError,
    _DIRECT_OPENER,
    confirm_draft,
    draft_fingerprint,
    local_urlopen,
    parse_chat_stream,
    prepare_draft,
    select_provider,
    suggest_inventory_object,
    validate_image,
    validate_object_suggestion,
    validate_suggestion,
)
from tests.contextflow_ollama_fakes import (
    SHOW_PLAIN,
    _chat_bodies,
    _chat_requests,
    _local_route,
)
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from app.site_visit_store import SiteVisitStore

MOCK_SETTINGS = {"provider": "mock"}


def _png_bytes(size: int = 4) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (size, size), color=(200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _visit_with_asset(root: Path, actor: str) -> tuple[str, str]:
    store = SiteVisitStore(root)
    record = store.create(
        actor,
        {
            "title": "Ortstermin Foto (synthetisch)",
            "customer": "Musterkunde (synthetisch)",
            "site": "Werkstatt (synthetisch)",
            "visit_date": "2026-10-20T10:00",
        },
    )
    visit_id = str(record["visit_id"])
    after = store.add_asset(
        visit_id,
        actor,
        {"name": "Buchscanner (synthetisch)", "kind": "Buchscanner"},
    )
    return visit_id, str(after["assets"][-1]["asset_id"])


class VisionBackendTest(unittest.TestCase):
    actor = "cf-vision"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare(self, visit_id: str, asset_id: str = "", **overrides):
        params = {
            "visit_id": visit_id,
            "asset_id": asset_id,
            "filename": "foto.png",
            "content_type": "image/png",
            "stream": BytesIO(_png_bytes()),
            "settings": dict(MOCK_SETTINGS),
        }
        params.update(overrides)
        return prepare_draft(self.root, self.actor, **params)

    def test_mock_draft_deterministic_and_labeled(self) -> None:
        provider = MockVisionProvider()
        first = provider.suggest(b"x", "image/png", {"visit_title": "V"})
        second = provider.suggest(b"y", "image/jpeg", {"visit_title": "V"})
        self.assertEqual(first, second)
        self.assertIn("Mock", provider.label)
        self.assertIn("Mock", first["title"])

    def test_mock_draft_reports_mock_model(self) -> None:
        visit_id, asset_id = _visit_with_asset(self.root, self.actor)
        draft = self._prepare(visit_id, asset_id)
        self.assertEqual("mock", draft["provider"])
        self.assertEqual("mock", draft["model"])
        self.assertIn("Mock", draft["provider_label"])

    def test_image_validation(self) -> None:
        png = _png_bytes()
        self.assertEqual(
            validate_image("foto.png", "image/png", BytesIO(png)), png
        )
        buffer = BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, format="JPEG")
        self.assertTrue(
            validate_image("foto.jpg", "image/jpeg", BytesIO(buffer.getvalue()))
        )
        bad = [
            ("", "image/png", png),
            ("foto.txt", "text/plain", png),
            ("foto.png", "image/png", b""),
            ("foto.png", "image/png", b"kein bild"),
            ("foto.png", "image/gif", png),
        ]
        for filename, mime, data in bad:
            with self.assertRaises(VisionError, msg=(filename, mime)):
                validate_image(filename, mime, BytesIO(data))
        with self.assertRaises(VisionError):
            validate_image("foto.png", "image/png", BytesIO(b"\xff" * (6 * 1024 * 1024)))

    def test_schema_validation(self) -> None:
        draft = validate_suggestion(
            {"title": "  Titel  ", "description": " D ",
             "evidence_class": "observed", "confidence": 42}
        )
        self.assertEqual(
            draft,
            {"title": "Titel", "description": "D",
             "evidence_class": "observed", "confidence": 42},
        )
        minimal = validate_suggestion({"title": "T"})
        self.assertEqual(minimal["evidence_class"], "unknown")
        self.assertEqual(minimal["confidence"], 0)
        for payload in (
            [],
            {"title": ""},
            {"title": "x" * 241},
            {"title": "T", "evidence_class": "raten"},
            {"title": "T", "confidence": -1},
            {"title": "T", "confidence": 101},
            {"title": "T", "confidence": True},
            {"title": "T", "confidence": "50"},
        ):
            with self.assertRaises(VisionError, msg=repr(payload)):
                validate_suggestion(payload)

    def test_fingerprint_stable_and_sensitive(self) -> None:
        draft = validate_suggestion({"title": "T"})
        first = draft_fingerprint("v", "a", draft)
        self.assertEqual(first, draft_fingerprint("v", "a", draft))
        other = validate_suggestion({"title": "T "})
        self.assertEqual(first, draft_fingerprint("v", "a", other))
        changed = validate_suggestion({"title": "U"})
        self.assertNotEqual(first, draft_fingerprint("v", "a", changed))
        self.assertNotEqual(first, draft_fingerprint("v", "b", draft))

    def test_prepare_rights_and_pairing(self) -> None:
        visit_id, asset_id = _visit_with_asset(self.root, self.actor)
        draft = self._prepare(visit_id, asset_id)
        self.assertEqual(draft["provider"], "mock")
        self.assertEqual(draft["asset_id"], asset_id)
        self.assertIn("Buchscanner", draft["asset_name"])
        other_visit, _ = _visit_with_asset(self.root, self.actor)
        # Denied outcome as the existing store reports it (owner-only visits
        # raise ValueError 'Ortstermin nicht gefunden', never a leak).
        with self.assertRaises((PermissionError, ValueError)):
            prepare_draft(
                self.root, "cf-fremd", visit_id=visit_id,
                filename="f.png", content_type="image/png",
                stream=BytesIO(_png_bytes()), settings=dict(MOCK_SETTINGS),
            )
        with self.assertRaises(ValueError):
            self._prepare(other_visit, asset_id)
        with self.assertRaises((PermissionError, ValueError)):
            self._prepare("besuch-fehlt")

    def test_confirm_creates_verified_finding(self) -> None:
        visit_id, asset_id = _visit_with_asset(self.root, self.actor)
        draft = self._prepare(visit_id, asset_id)
        created = confirm_draft(
            self.root,
            self.actor,
            visit_id=visit_id,
            asset_id=asset_id,
            expected_fingerprint=draft["fingerprint"],
            **draft["draft"],
        )
        record = SiteVisitStore(self.root).get(visit_id, self.actor)
        stored = record["findings"][-1]
        self.assertEqual(stored["finding_id"], created["finding_id"])
        self.assertEqual(stored["title"], draft["draft"]["title"])
        self.assertEqual(stored["asset_id"], asset_id)
        self.assertEqual(stored["status"], "unreviewed")

    def test_confirm_mismatch_and_denied(self) -> None:
        visit_id, asset_id = _visit_with_asset(self.root, self.actor)
        draft = self._prepare(visit_id, asset_id)
        with self.assertRaises(ApprovalStale):
            confirm_draft(
                self.root, self.actor, visit_id=visit_id, asset_id=asset_id,
                expected_fingerprint="0" * 64, **draft["draft"],
            )
        tampered = dict(draft["draft"])
        tampered["title"] = "Geändert"
        with self.assertRaises(ApprovalStale):
            confirm_draft(
                self.root, self.actor, visit_id=visit_id, asset_id=asset_id,
                expected_fingerprint=draft["fingerprint"], **tampered,
            )
        with self.assertRaises((PermissionError, ValueError)):
            confirm_draft(
                self.root, "cf-fremd", visit_id=visit_id, asset_id=asset_id,
                expected_fingerprint=draft["fingerprint"], **draft["draft"],
            )
        record = SiteVisitStore(self.root).get(visit_id, self.actor)
        self.assertEqual(record["findings"], [])

    def test_no_image_bytes_persisted(self) -> None:
        visit_id, asset_id = _visit_with_asset(self.root, self.actor)
        png = _png_bytes()
        before = self._files_with_content()
        prepare_draft(
            self.root, self.actor, visit_id=visit_id, asset_id=asset_id,
            filename="foto.png", content_type="image/png",
            stream=BytesIO(png), settings=dict(MOCK_SETTINGS),
        )
        after = self._files_with_content()
        self.assertEqual(set(before), set(after))
        for _, content in after.items():
            self.assertNotIn(png, content)

    def _files_with_content(self) -> dict[str, bytes]:
        found = {}
        for path in self.root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()
                if len(data) <= 1024 * 1024:
                    found[str(path.relative_to(self.root))] = data
        return found

    def test_meta_gates(self) -> None:
        visit_id, asset_id = _visit_with_asset(self.root, self.actor)
        base = {
            "provider": "meta",
            "meta_consent": True,
            "meta_api_key": "key",
            "meta_endpoint": META_MODEL_API_ENDPOINT,
            "meta_model": META_MODEL_API_MODEL,
        }
        image = {"filename": "f.png", "content_type": "image/png",
                 "stream": BytesIO(_png_bytes())}
        with self.assertRaises(PermissionError):
            prepare_draft(self.root, self.actor, visit_id=visit_id,
                          asset_id=asset_id, consent_meta=False,
                          settings=dict(base), **image)
        image["stream"] = BytesIO(_png_bytes())
        with self.assertRaises(PermissionError):
            prepare_draft(self.root, self.actor, visit_id=visit_id,
                          asset_id=asset_id, consent_meta=True,
                          settings=dict(base), **image)
        demo = Path(self.temp.name) / "demo-root"
        demo.mkdir()
        (demo / DEMO_MARKER).write_text(
            json.dumps({"synthetic": True, "tool": "contextflow_demo"}),
            encoding="utf-8",
        )
        visit2, _ = _visit_with_asset(demo, self.actor)
        image["stream"] = BytesIO(_png_bytes())
        keyless = dict(base)
        keyless["meta_api_key"] = ""
        with self.assertRaises(PermissionError):
            prepare_draft(demo, self.actor, visit_id=visit2,
                          consent_meta=True, settings=keyless, **image)

    def test_meta_positive_only_via_mocked_transport(self) -> None:
        suggestion = {"title": "T", "description": "D",
                      "evidence_class": "observed", "confidence": 50}
        payload = json.dumps(
            {
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": json.dumps(suggestion)}
                        ],
                    }
                ],
            }
        ).encode("utf-8")
        response = mock.MagicMock()
        response.read.return_value = payload
        context = mock.MagicMock()
        context.__enter__.return_value = response
        context.__exit__.return_value = False
        transport = mock.MagicMock()
        transport.open.return_value = context
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=transport,
        ) as build_opener:
            out = MetaVisionProvider(
                "k", META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL
            ).suggest(b"x", "image/png", {})
        self.assertEqual(out, suggestion)
        build_opener.assert_called_once()
        sent = transport.open.call_args[0][0]
        self.assertEqual(sent.get_header("Authorization"), "Bearer k")
        body = json.loads(sent.data.decode("utf-8"))
        self.assertEqual(sent.full_url, META_MODEL_API_ENDPOINT)
        self.assertEqual(body["model"], META_MODEL_API_MODEL)
        parts = body["input"][0]["content"]
        self.assertEqual(parts[0]["type"], "input_text")
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertTrue(
            parts[1]["image_url"].startswith("data:image/png;base64,")
        )

    def test_local_backend_guards(self) -> None:
        for bad_url in ("https://example.com", "http://example.com",
                        "ftp://127.0.0.1/x", "keine-url"):
            with self.assertRaises(VisionError, msg=bad_url):
                LocalVisionProvider(bad_url, "llama3.2-vision:11b")
        with self.assertRaises(VisionError):
            LocalVisionProvider("http://127.0.0.1:11434", "")
        refused = LocalVisionProvider(
            "http://127.0.0.1:9", "llama3.2-vision:11b", timeout=2
        )
        with self.assertRaises(InferenceUnavailable):
            refused.suggest(b"x", "image/png", {})
        with self.assertRaises(VisionError):
            select_provider({"provider": "raten"}, False, self.root)

    def test_runtime_rejects_non_meta_and_text_only_image_models(self) -> None:
        with mock.patch("app.contextflow.vision.local_urlopen") as transport:
            with self.assertRaises(VisionError):
                LocalVisionProvider("http://127.0.0.1:11434", "qwen3-vl:2b")
            with self.assertRaises(VisionError) as raised:
                LocalVisionProvider(
                    "http://127.0.0.1:11434", "llama3.2:1b"
                ).suggest(b"x", "image/png", {})
        self.assertIn("nur Text", str(raised.exception))
        transport.assert_not_called()

    def test_meta_provider_pins_official_endpoint_and_model(self) -> None:
        with self.assertRaises(VisionError):
            MetaVisionProvider(
                "k", "https://api.attacker.invalid/v1/chat/completions",
                META_MODEL_API_MODEL,
            )
        with self.assertRaises(VisionError):
            MetaVisionProvider("k", META_MODEL_API_ENDPOINT, "non-meta-model")

    def test_meta_transport_does_not_follow_redirects(self) -> None:
        from app.contextflow.vision import _NoRedirectHandler

        handler = _NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                None, None, 302, "Found", {}, "https://attacker.invalid/"
            )
        )


class ObjectSuggestionTest(unittest.TestCase):
    def test_inventory_path_blocks_mock(self) -> None:
        with self.assertRaises(MockBlockedError) as raised:
            suggest_inventory_object(
                b"x", "image/png", {}, dict(MOCK_SETTINGS)
            )
        message = str(raised.exception)
        self.assertIn("Meta-Modell", message)
        self.assertNotIn("Hammer", message)

    def test_object_schema(self) -> None:
        good = validate_object_suggestion(
            {"object_name": "  Hammer  ", "item_type": "TOOL",
             "confidence": 80, "note": " n "}
        )
        self.assertEqual(
            good,
            {"object_name": "Hammer", "item_type": "tool",
             "confidence": 80, "note": "n", "isbn": ""},
        )
        self.assertEqual(
            validate_object_suggestion({"object_name": "X"})["item_type"],
            "object",
        )
        self.assertEqual(
            validate_object_suggestion(
                {"object_name": "X", "item_type": "ratespiel"}
            )["item_type"],
            "object",
        )
        for payload in (
            [],
            {"object_name": ""},
            {"object_name": "x" * 121},
            {"object_name": "X", "confidence": -1},
            {"object_name": "X", "confidence": 101},
            {"object_name": "X", "confidence": True},
            {"object_name": "X", "note": "x" * 501},
        ):
            with self.assertRaises(VisionError, msg=repr(payload)):
                validate_object_suggestion(payload)

    def test_object_isbn_valid_normalized(self) -> None:
        self.assertEqual(
            validate_object_suggestion(
                {"object_name": "X", "isbn": "0-13-110362-8"}
            )["isbn"],
            "9780131103627",
        )
        self.assertEqual(
            validate_object_suggestion(
                {"object_name": "X", "isbn": "978-0-13-110362-7"}
            )["isbn"],
            "9780131103627",
        )

    def test_object_isbn_invalid_discarded(self) -> None:
        for raw in ("9780131103628", "keine-ahnung", "123", "", None, True):
            with self.subTest(raw=raw):
                out = validate_object_suggestion(
                    {"object_name": "X", "isbn": raw}
                )
                self.assertEqual(out["isbn"], "")
        dumped = json.dumps(
            validate_object_suggestion(
                {"object_name": "X", "isbn": "9780131103628"}
            )
        )
        self.assertNotIn("9780131103628", dumped)

    def test_object_isbn_missing_defaults_empty(self) -> None:
        out = validate_object_suggestion({"object_name": "X"})
        self.assertEqual(out["isbn"], "")
        self.assertEqual(out["object_name"], "X")

    def test_object_isbn_roundtrip_and_no_guess_prompt(self) -> None:
        answer = json.dumps(
            {"object_name": "Buch", "item_type": "book",
             "confidence": 90, "note": "", "isbn": "0-13-110362-8"}
        )
        chunks = [
            {"message": {"role": "assistant", "content": answer[:40]}},
            {
                "message": {"role": "assistant", "content": answer[40:]},
                "done": True,
            },
        ]
        settings = {
            "provider": "local",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2-vision:11b",
        }
        route = _local_route(show=SHOW_PLAIN, chat=chunks)
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            out = suggest_inventory_object(b"x", "image/png", {}, settings)
        self.assertEqual(out["suggestion"]["isbn"], "9780131103627")
        sent = json.loads(opener.call_args[0][0].data.decode("utf-8"))
        content = sent["messages"][0]["content"]
        self.assertIn("isbn", content)
        self.assertIn("niemals raten", content)

    def test_inventory_path_refuses_cloud_without_consent(self) -> None:
        settings = {
            "provider": "meta",
            "meta_consent": True,
            "meta_api_key": "key",
            "meta_endpoint": META_MODEL_API_ENDPOINT,
            "meta_model": META_MODEL_API_MODEL,
        }
        with self.assertRaises(PermissionError):
            suggest_inventory_object(
                b"x", "image/png", {}, settings, consent_meta=False
            )

    def test_local_chat_streams_bounded_and_omits_think(self) -> None:
        settings = {
            "provider": "local",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2-vision:11b",
        }
        chunks = [
            {
                "message": {
                    "role": "assistant",
                    "thinking": "lange Überlegung …",
                    "content": '{"object_name": "Ham',
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": 'mer", "item_type": "tool", "confidence": 90, "note": ""}',
                },
                "done": True,
            },
        ]
        route = _local_route(show=SHOW_PLAIN, chat=chunks)
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            out = suggest_inventory_object(b"x", "image/png", {}, settings)
        self.assertEqual(out["suggestion"]["object_name"], "Hammer")
        chats = _chat_requests(opener)
        self.assertEqual(1, len(chats))
        sent = chats[0]
        self.assertTrue(sent.full_url.endswith("/api/chat"))
        bodies = _chat_bodies(opener)
        self.assertEqual(1, len(bodies))
        self.assertEqual(
            {"model", "messages", "stream", "format", "options"},
            set(bodies[0]),
        )
        self.assertIs(True, bodies[0]["stream"])
        self.assertEqual("json", bodies[0]["format"])
        self.assertEqual({"num_predict": OLLAMA_NUM_PREDICT}, bodies[0]["options"])
        self.assertEqual(2048, bodies[0]["options"]["num_predict"])
        self.assertNotIn("think", bodies[0])
        self.assertNotIn("think", bodies[0]["options"])
        self.assertEqual(1, len(bodies[0]["messages"]))
        user = bodies[0]["messages"][0]
        self.assertEqual("user", user["role"])
        self.assertEqual(["eA=="], user["images"])
        self.assertTrue(user["content"])

    def test_local_empty_response_reports_german_error(self) -> None:
        settings = {
            "provider": "local",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2-vision:11b",
        }
        variants = [
            [{"message": {"role": "assistant", "content": "   "}, "done": True}],
            [{"message": {"role": "assistant", "content": ""}, "done": True}],
            [{"message": {"role": "assistant"}, "done": True}],
            [{"done": True}],
            [
                {"message": {"role": "assistant", "thinking": "nur gedacht"}},
                {
                    "message": {"role": "assistant", "thinking": "weiter gedacht"},
                    "done": True,
                },
            ],
        ]
        for chunks in variants:
            with self.subTest(chunks=chunks):
                route = _local_route(show=SHOW_PLAIN, chat=chunks)
                with mock.patch(
                    "app.contextflow.vision.local_urlopen", side_effect=route
                ):
                    with self.assertRaises(VisionError) as raised:
                        suggest_inventory_object(b"x", "image/png", {}, settings)
                self.assertIn("leer geantwortet", str(raised.exception))

    def test_local_timeout_reports_time_limit(self) -> None:
        with mock.patch(
            "app.contextflow.vision.local_urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            with self.assertRaises(InferenceTimeoutError) as raised:
                LocalVisionProvider(
                    "http://127.0.0.1:11434", "llama3.2-vision:11b", timeout=60
                ).suggest(b"x", "image/png", {})
        self.assertIn("Zeitlimit", str(raised.exception))

    def test_local_object_prompt_reaches_backend(self) -> None:
        answer = json.dumps(
            {"object_name": "Hammer", "item_type": "tool",
             "confidence": 90, "note": ""}
        )
        chunks = [
            {"message": {"role": "assistant", "content": answer}, "done": True}
        ]
        settings = {
            "provider": "local",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2-vision:11b",
        }
        route = _local_route(show=SHOW_PLAIN, chat=chunks)
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            out = suggest_inventory_object(
                b"x", "image/png", {"barcode": "123"}, settings
            )
        self.assertEqual(out["suggestion"]["object_name"], "Hammer")
        sent = json.loads(opener.call_args[0][0].data.decode("utf-8"))
        content = sent["messages"][0]["content"]
        self.assertIn("object_name", content)
        self.assertIn("123", content)


class ChatStreamParserTest(unittest.TestCase):
    def _stream(self, chunks) -> BytesIO:
        lines = []
        for chunk in chunks:
            if isinstance(chunk, bytes):
                lines.append(chunk if chunk.endswith(b"\n") else chunk + b"\n")
            else:
                lines.append(
                    json.dumps(chunk, ensure_ascii=False).encode("utf-8") + b"\n"
                )
        return BytesIO(b"".join(lines))

    def _parse(self, chunks, **overrides):
        params: dict = {"timeout_seconds": 60, "deadline": time.monotonic() + 30}
        params.update(overrides)
        return parse_chat_stream(self._stream(chunks), **params)

    def test_multi_chunk_accumulates_content_and_ignores_thinking(self) -> None:
        answer = self._parse(
            [
                {"message": {"thinking": "na", "content": '{"a": '}},
                b"\n",
                {"message": {"content": "1}", "thinking": "fertig"}},
                {"done": True},
            ]
        )
        self.assertEqual('{"a": 1}', answer)

    def test_thought_only_stream_reports_empty_german_error(self) -> None:
        with self.assertRaises(VisionError) as raised:
            self._parse(
                [
                    {"message": {"thinking": "grübel"}},
                    {"message": {"thinking": "grübel weiter"}, "done": True},
                ]
            )
        self.assertIn("leer geantwortet", str(raised.exception))

    def test_malformed_chunk_reports_invalid_german_error(self) -> None:
        for raw in (b"kein json\n", b"[1, 2]\n"):
            with self.subTest(raw=raw):
                with self.assertRaises(VisionError) as raised:
                    parse_chat_stream(
                        BytesIO(raw),
                        timeout_seconds=60,
                        deadline=time.monotonic() + 30,
                    )
                self.assertIn("ungültig", str(raised.exception))

    def test_eof_without_done_reports_incomplete(self) -> None:
        with self.assertRaises(InferenceUnavailable) as raised:
            self._parse([{"message": {"content": '{"a": 1}'}}])
        self.assertIn("unvollständig", str(raised.exception))
        self.assertNotIsInstance(raised.exception, InferenceTimeoutError)

    def test_stream_byte_cap_refuses_runaway_response(self) -> None:
        with self.assertRaises(InferenceUnavailable) as raised:
            self._parse(
                [{"message": {"content": "x" * 100}, "done": True}],
                max_bytes=10,
            )
        self.assertIn("Stream-Limit", str(raised.exception))

    def test_content_cap_refuses_runaway_answer(self) -> None:
        with self.assertRaises(InferenceUnavailable) as raised:
            self._parse(
                [{"message": {"content": "y" * 100}, "done": True}],
                max_chars=10,
            )
        self.assertIn("Inhalts-Limit", str(raised.exception))

    def test_expired_deadline_reports_time_limit(self) -> None:
        with self.assertRaises(InferenceTimeoutError) as raised:
            self._parse(
                [{"message": {"content": "x"}, "done": True}],
                deadline=time.monotonic() - 1,
            )
        self.assertIn("Zeitlimit", str(raised.exception))

    def test_local_transport_ignores_proxy_environment(self) -> None:
        proxies = [
            handler.proxies
            for handler in _DIRECT_OPENER.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertTrue(all(mapping == {} for mapping in proxies))

    def test_local_urlopen_uses_direct_opener(self) -> None:
        request = urllib.request.Request("http://127.0.0.1:11434/api/show")
        with mock.patch.object(_DIRECT_OPENER, "open", return_value="OK") as opened:
            self.assertEqual("OK", local_urlopen(request, timeout=5))
        opened.assert_called_once_with(request, timeout=5)


class VisionWebTest(unittest.TestCase):
    password = "contextflow-vision-web-test-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "TESTING",
                "TEST_CSRF_PROTECTION", "CONTEXTFLOW_ENABLED",
                "CONTEXTFLOW_VISION_PROVIDER",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="mock",
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
        self.visit_id, self.asset_id = _visit_with_asset(self.root, "admin")

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

    def _hidden(self, body: bytes, name: str) -> str:
        match = re.search(
            rb'name="%s" value="(.*?)"' % name.encode(), body, re.DOTALL
        )
        self.assertIsNotNone(match, name)
        assert match is not None
        return match.group(1).decode("utf-8")

    def test_flag_off_404(self) -> None:
        token = self.csrf(self.admin)
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            self.assertEqual(
                self.admin.post(
                    "/contextflow/suggest", data={"_csrf_token": token}
                ).status_code,
                404,
            )
            self.assertEqual(
                self.admin.post(
                    "/contextflow/suggest/confirm", data={"_csrf_token": token}
                ).status_code,
                404,
            )
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)

    def test_suggest_without_token_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/suggest",
            data={"visit_id": self.visit_id},
            content_type="multipart/form-data",
        )
        self.assertIn(response.status_code, (400, 403))

    def test_suggest_confirm_happy(self) -> None:
        token = self.csrf(self.admin)
        response = self.admin.post(
            "/contextflow/suggest",
            data={
                "visit_id": self.visit_id,
                "asset_id": self.asset_id,
                "_csrf_token": token,
                "photo": (BytesIO(_png_bytes()), "foto.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn("Demo-Vorschlag bitte prüfen".encode("utf-8"), body)
        self.assertIn("Demo-Mock".encode("utf-8"), body)
        payload = {
            name: self._hidden(body, name)
            for name in (
                "visit_id", "asset_id", "title", "description",
                "evidence_class", "confidence", "expected_fingerprint",
            )
        }
        payload["approved"] = "yes"
        payload["_csrf_token"] = token
        done = self.admin.post("/contextflow/suggest/confirm", data=payload)
        self.assertEqual(done.status_code, 302)
        self.assertIn(f"/site-visits/{self.visit_id}", done.headers["Location"])
        record = SiteVisitStore(self.root).get(self.visit_id, "admin")
        stored = record["findings"][-1]
        self.assertEqual(stored["asset_id"], self.asset_id)
        self.assertEqual(stored["status"], "unreviewed")
        self.assertIn("Demo-Vorschlag", stored["title"])
        leftovers = [
            path for path in self.root.rglob("*")
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        self.assertEqual(leftovers, [])

    def test_suggest_confirm_mismatch_conflict(self) -> None:
        token = self.csrf(self.admin)
        response = self.admin.post(
            "/contextflow/suggest",
            data={
                "visit_id": self.visit_id,
                "_csrf_token": token,
                "photo": (BytesIO(_png_bytes()), "foto.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = {
            name: self._hidden(response.data, name)
            for name in (
                "visit_id", "asset_id", "title", "description",
                "evidence_class", "confidence", "expected_fingerprint",
            )
        }
        payload["title"] = "Geändert"
        payload["approved"] = "yes"
        payload["_csrf_token"] = token
        self.assertEqual(
            self.admin.post("/contextflow/suggest/confirm", data=payload).status_code,
            409,
        )

    def test_suggest_foreign_visit_denied(self) -> None:
        response = self.berta.post(
            "/contextflow/suggest",
            data={
                "visit_id": self.visit_id,
                "_csrf_token": self.csrf(self.berta),
                "photo": (BytesIO(_png_bytes()), "foto.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertIn(response.status_code, (400, 403))

    def test_visit_detail_shows_prefilled_photo_form_when_enabled(self) -> None:
        response = self.admin.get(f"/site-visits/{self.visit_id}")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b"/contextflow/suggest", body)
        self.assertIn(
            f'name="visit_id" value="{self.visit_id}"'.encode(), body
        )
        self.assertIn(
            f'name="asset_id" value="{self.asset_id}"'.encode(), body
        )
        self.assertIn("Buchscanner (synthetisch)".encode(), body)
        self.assertIn(
            "KI-Entwurf anfragen".encode("utf-8"), body
        )
        self.assertIn(b"Foto / Beleg", body)

    def test_visit_detail_omits_photo_form_when_disabled(self) -> None:
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            response = self.admin.get(f"/site-visits/{self.visit_id}")
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"/contextflow/suggest", response.data)
        self.assertIn(b"Foto / Beleg", response.data)

    def test_inventory_suggest_mock_returns_503(self) -> None:
        response = self.admin.post(
            "/contextflow/inventory-suggest",
            data={
                "_csrf_token": self.csrf(self.admin),
                "barcode": "4251234567890",
                "photo": (BytesIO(_png_bytes()), "foto.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertIn("Meta-Modell", payload["error"])
        self.assertNotIn(b"Hammer", response.data)

    def test_inventory_suggest_rejects_bad_image(self) -> None:
        response = self.admin.post(
            "/contextflow/inventory-suggest",
            data={
                "_csrf_token": self.csrf(self.admin),
                "photo": (BytesIO(b"kein bild"), "foto.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_inventory_suggest_without_token_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/inventory-suggest",
            data={"photo": (BytesIO(_png_bytes()), "foto.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertIn(response.status_code, (400, 403))

    def test_inventory_suggest_flag_off_404(self) -> None:
        token = self.csrf(self.admin)
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            response = self.admin.post(
                "/contextflow/inventory-suggest",
                data={
                    "_csrf_token": token,
                    "photo": (BytesIO(_png_bytes()), "foto.png", "image/png"),
                },
                content_type="multipart/form-data",
            )
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
