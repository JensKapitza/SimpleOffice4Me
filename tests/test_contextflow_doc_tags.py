"""Local-AI tag suggestions for imported documents (tags + image + confirm).

The prompt carries metadata plus locally extracted text to the on-machine
model only; image notes arrive through a separate parallel call, and
nothing is stored without explicit confirmation.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from app import db as database
from app.contextflow.doc_tags import (
    IMAGE_INSTRUCTION,
    TAG_NUM_PREDICT,
    DocTagError,
    build_tag_prompt,
    document_image_bytes,
    image_mime_type,
    suggest_image_for_document,
    suggest_tags_for_document,
    validate_image_description,
    validate_tag_suggestions,
)
from app.contextflow.vision import META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL
from app.document_store import DocumentStore
from tests.test_contextflow_inventory_match import PNG_1PX

CSRF_TOKEN = "doc-engel-csrf-token-long-enough"
TEXT_CHUNKS = [
    {
        "message": {
            "role": "assistant",
            "thinking": "Der Nutzer will Tags; ich prüfe Textbelege.",
            "content": '{"tags": [{"tag": "Rechnung", "reason": "Der Text nennt einen Rechnungsbetrag.", "evidence": "Rechnung über 120 Euro"}, {"tag": "120 Euro", "reason": "Der Betrag',
        }
    },
    {
        "message": {
            "role": "assistant",
            "content": ' steht im Text.", "evidence": "120 Euro"}]}',
        },
        "done": True,
    },
]
TEXT_MIXED_CHUNKS = [
    {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {"tags": [
                    {"tag": "Rechnung", "reason": "Belegt.", "evidence": "Rechnung über 120 Euro"},
                    {"tag": "Einkommen", "reason": "Belegt.", "evidence": "Rechnung über 120 Euro"},
                    {"tag": "kostenrechnung", "reason": "Belegt.", "evidence": "Rechnung über 120 Euro"},
                ]}
            ),
        },
        "done": True,
    },
]
VISION_CHUNKS = [
    {
        "message": {
            "role": "assistant",
            "thinking": "Ich schaue mir das Bild an.",
            "content": '{"description": "Ein Schreibtisch',
        }
    },
    {
        "message": {
            "role": "assistant",
            "content": ' mit Bank.", "objects": ["Bank", "Stift"]}',
        },
        "done": True,
    },
]
VISION_PARTS_CHUNKS = [
    {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "description": "Ein Stuhl aus Holz.",
                    "objects": ["Sitzfläche", "Stützen"],
                }
            ),
        },
        "done": True,
    },
]
TEXT_WEAK_CHUNKS = [
    {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {"tags": [
                    {"tag": "aus", "reason": "B.", "evidence": "aus dem Jahr"},
                    {"tag": "notizen", "reason": "B.", "evidence": "notizen.txt"},
                    {"tag": "Euro", "reason": "B.", "evidence": "120 Euro"},
                ]}
            ),
        },
        "done": True,
    },
]
TEXT_EVIDENCE_CHUNKS = [
    {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {"tags": [{"tag": "Rechnung", "reason": "Belegt.", "evidence": "120 Euro"}]}
            ),
        },
        "done": True,
    },
]
TEXT_INVENTED_CHUNKS = [
    {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {"tags": [{"tag": "Rechnung", "reason": "Belegt.", "evidence": "Spalte D"}]}
            ),
        },
        "done": True,
    },
]


def _chat_requests(opened) -> list:
    return [
        call.args[0]
        for call in opened.call_args_list
        if (getattr(call.args[0], "full_url", "") or "").rstrip("/").endswith("/api/chat")
    ]


def _chat_bodies(opened) -> list:
    return [json.loads(request.data.decode("utf-8")) for request in _chat_requests(opened)]


SHOW_PLAIN = {"capabilities": ["completion", "vision"]}


class _FakeJson:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        _ = size
        return self._body


class _FakeOllama:
    def __init__(self, chunks):
        self._stream = BytesIO(
            b"".join(json.dumps(chunk).encode("utf-8") + b"\n" for chunk in chunks)
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def readline(self, limit=-1):
        return self._stream.readline(limit)


class _StubStorage:
    """Verified-range storage double; records whether bytes were read."""

    def __init__(self, data: bytes, size: int | None = None, *, ok: bool = True):
        self._data = data
        self._size = len(data) if size is None else size
        self._ok = ok
        self.reads = 0

    def stat(self, object_id):
        _ = object_id
        return SimpleNamespace(ok=self._ok, value=SimpleNamespace(size=self._size, version=1))

    def copy_verified_range_to(self, object_id, spool, start=0, length=0):
        _ = (object_id, start, length)
        self.reads += 1
        spool.write(self._data)
        return SimpleNamespace(ok=True, value=SimpleNamespace(size=len(self._data), version=1))


class DocTagSuggestContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.document_root = root / "documents"
        keys = (
            "TESTING",
            "DATABASE",
            "DOCUMENT_ROOT",
            "TEST_CSRF_PROTECTION",
            "SECRET_KEY",
            "CONTEXTFLOW_ENABLED",
            "CONTEXTFLOW_VISION_PROVIDER",
            "CONTEXTFLOW_OLLAMA_URL",
            "CONTEXTFLOW_OLLAMA_MODEL",
            "CONTEXTFLOW_OLLAMA_TIMEOUT",
        )
        saved = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(self.document_root),
            TEST_CSRF_PROTECTION=True,
            SECRET_KEY="doc-engel-test-secret",
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="local",
            CONTEXTFLOW_OLLAMA_URL="http://127.0.0.1:11434",
            CONTEXTFLOW_OLLAMA_MODEL="llama3.2-vision:11b",
            CONTEXTFLOW_OLLAMA_TIMEOUT=60,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('doc-engel-member','unused',0)")
            db.commit()
            self.user_id = db.execute("SELECT id FROM user WHERE username='doc-engel-member'").fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = self.user_id
            state["_csrf_token"] = CSRF_TOKEN
        folder = self.document_root / "imports" / "agent_kit"
        folder.mkdir(parents=True)
        self.txt_path = folder / "notizen.txt"
        self.txt_path.write_text("Rechnung über 120 Euro aus dem Jahr 2024.", encoding="utf-8")
        self.png_path = folder / "foto.png"
        self.png_path.write_bytes(PNG_1PX)
        store = DocumentStore(self.document_root)
        store.scan()
        self.txt_id = store.get_document(self.txt_path)["document_id"]
        self.png_id = store.get_document(self.png_path)["document_id"]

    def suggest(self, document_id, **extra):
        data = {"_csrf_token": CSRF_TOKEN, "document_id": document_id, "folder": "imports/agent_kit"}
        data.update(extra)
        return self.client.post("/contextflow/doc-tag-suggest", data=data)

    def image_suggest(self, document_id):
        return self.client.post(
            "/contextflow/doc-image-suggest",
            data={"_csrf_token": CSRF_TOKEN, "document_id": document_id},
        )

    def ollama(self, payloads, *, show=SHOW_PLAIN, tags=None, calls=None):
        queue = list(payloads)
        tag_payload = {"models": []} if tags is None else tags

        def _answer(suffix):
            if suffix == "/api/show":
                spec = show
            elif suffix == "/api/tags":
                spec = tag_payload
            else:
                return _FakeOllama(queue.pop(0))
            if isinstance(spec, Exception):
                raise spec
            return _FakeJson(spec)

        def _route(request, timeout=None, **kwargs):
            _ = (timeout, kwargs)
            url = (getattr(request, "full_url", "") or "").rstrip("/")
            for suffix in ("/api/show", "/api/tags", "/api/chat"):
                if url.endswith(suffix):
                    if calls is not None:
                        calls.append(suffix)
                    return _answer(suffix)
            raise AssertionError(f"unexpected local URL: {url}")

        return patch("app.contextflow.vision.local_urlopen", side_effect=_route)

    def test_prompt_contains_metadata_extracted_text_and_guiding_question(self):
        prompt = build_tag_prompt(
            filename="notizen.txt",
            tags=["Import"],
            folder_hint="imports/agent_kit",
            text="Rechnung über 120 Euro.",
        )

        self.assertIn("notizen.txt", prompt)
        self.assertIn("imports/agent_kit", prompt)
        self.assertIn("Import", prompt)
        self.assertIn("Rechnung über 120 Euro.", prompt)
        self.assertIn("Welche Tags würdest du hinzufügen?", prompt)

    def test_suggest_returns_validated_tags_and_stores_nothing(self):
        before = DocumentStore(self.document_root).get_document(self.txt_id).get("tags", [])
        with self.ollama([TEXT_CHUNKS]):
            response = self.suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("Meta-Modell lokal via Ollama", payload["provider_label"])
        self.assertIn(payload["text_source"], ("extracted", "file"))
        self.assertEqual(
            ["Rechnung", "120 Euro"], [row["tag"] for row in payload["suggestions"]]
        )
        self.assertTrue(all(row["reason"] for row in payload["suggestions"]))
        self.assertNotIn("image", payload)
        self.assertNotIn("image_error", payload)
        after = DocumentStore(self.document_root).get_document(self.txt_id).get("tags", [])
        self.assertEqual(before, after)

    def test_non_loopback_backend_is_refused_without_network(self):
        app.config.update(CONTEXTFLOW_OLLAMA_URL="http://example.com:11434")

        with patch("app.contextflow.vision.local_urlopen") as opened:
            response = self.suggest(self.txt_id)

        self.assertEqual(400, response.status_code)
        self.assertIn("loopback", response.get_json()["error"])
        opened.assert_not_called()

    def test_meta_provider_is_refused_without_consent(self):
        app.config.update(CONTEXTFLOW_VISION_PROVIDER="meta")

        with patch("app.contextflow.vision.local_urlopen") as opened:
            response = self.suggest(self.txt_id)

        self.assertEqual(403, response.status_code)
        opened.assert_not_called()

    def test_text_suggest_never_touches_vision(self):
        storage = _StubStorage(PNG_1PX)
        filename_chunks = [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {"tags": [{"tag": "Foto", "reason": "Dateiname.", "evidence": "foto.png"}]}
                    ),
                },
                "done": True,
            },
        ]
        with (
            patch("app.contextflow.doc_tags.storage_for", return_value=storage),
            self.ollama([filename_chunks]) as opened,
        ):
            response = self.suggest(self.png_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(1, len(_chat_bodies(opened)))
        self.assertEqual(0, storage.reads)
        self.assertNotIn("image", payload)

    def test_image_suggests_description_and_objects(self):
        storage = _StubStorage(PNG_1PX)
        with (
            patch("app.contextflow.doc_tags.storage_for", return_value=storage),
            self.ollama([VISION_CHUNKS]),
        ):
            response = self.image_suggest(self.png_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("Ein Schreibtisch mit Bank.", payload["image"]["description"])
        self.assertEqual(["Bank"], payload["image"]["objects"])
        self.assertEqual("Meta-Modell lokal via Ollama", payload["provider_label"])

    def test_image_suggest_reads_real_store_file_when_v2_missing(self):
        storage = _StubStorage(PNG_1PX, ok=False)
        before = DocumentStore(self.document_root).get_document(self.png_id)
        with (
            patch("app.contextflow.doc_tags.storage_for", return_value=storage),
            self.ollama([VISION_CHUNKS]),
        ):
            response = self.image_suggest(self.png_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("Ein Schreibtisch mit Bank.", payload["image"]["description"])
        self.assertEqual(["Bank"], payload["image"]["objects"])
        after = DocumentStore(self.document_root).get_document(self.png_id)
        self.assertEqual(before, after)

    def test_image_suggest_works_with_real_unpatched_storage(self):
        with self.ollama([VISION_CHUNKS]):
            response = self.image_suggest(self.png_id)

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])

    def test_oversize_store_file_answers_400(self):
        big_path = self.document_root / "imports" / "agent_kit" / "gross.png"
        big_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (6 * 1024 * 1024))
        store = DocumentStore(self.document_root)
        store.scan()
        big_id = store.get_document(big_path)["document_id"]
        storage = _StubStorage(b"", ok=False)
        with (
            patch("app.contextflow.doc_tags.storage_for", return_value=storage),
            self.ollama([VISION_CHUNKS]),
        ):
            response = self.image_suggest(big_id)

        self.assertEqual(400, response.status_code)
        self.assertIn("5 MiB", response.get_json()["error"])

    def test_deleted_store_file_answers_400(self):
        victim = self.document_root / "imports" / "agent_kit" / "weg.png"
        victim.write_bytes(PNG_1PX)
        store = DocumentStore(self.document_root)
        store.scan()
        victim_id = store.get_document(victim)["document_id"]
        victim.unlink()
        storage = _StubStorage(b"", ok=False)
        with (
            patch("app.contextflow.doc_tags.storage_for", return_value=storage),
            self.ollama([VISION_CHUNKS]),
        ):
            response = self.image_suggest(victim_id)

        self.assertEqual(400, response.status_code)
        self.assertIn("nicht verfügbar", response.get_json()["error"])

    def test_filesystem_safeguards_reject_unsafe_paths(self):
        storage = _StubStorage(b"", ok=False)
        with patch("app.contextflow.doc_tags.storage_for", return_value=storage):
            for last_path in ("../evil.png", "..\\evil.png", "/abs/evil.png", "C:/evil.png"):
                with self.subTest(last_path=last_path):
                    with self.assertRaises(DocTagError) as raised:
                        document_image_bytes(
                            root=self.document_root,
                            actor="doc-engel-member",
                            document={"document_id": "x", "last_path": last_path},
                        )
                    self.assertIn("ungültig", str(raised.exception))

    def test_directory_with_image_suffix_is_rejected(self):
        tricky = self.document_root / "imports" / "agent_kit" / "ordner.png"
        tricky.mkdir()
        storage = _StubStorage(b"", ok=False)
        with patch("app.contextflow.doc_tags.storage_for", return_value=storage):
            with self.assertRaises(DocTagError) as raised:
                document_image_bytes(
                    root=self.document_root,
                    actor="doc-engel-member",
                    document={
                        "document_id": "x",
                        "last_path": "imports/agent_kit/ordner.png",
                    },
                )
        self.assertIn("ungültig", str(raised.exception))

    def test_symlink_escape_is_rejected(self):
        outside = Path(self.temp.name) / "outside.png"
        outside.write_bytes(PNG_1PX)
        link = self.document_root / "imports" / "agent_kit" / "link.png"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink privilege missing")
        storage = _StubStorage(b"", ok=False)
        with patch("app.contextflow.doc_tags.storage_for", return_value=storage):
            with self.assertRaises(DocTagError) as raised:
                document_image_bytes(
                    root=self.document_root,
                    actor="doc-engel-member",
                    document={
                        "document_id": "x",
                        "last_path": "imports/agent_kit/link.png",
                    },
                )
        self.assertIn("ungültig", str(raised.exception))

    def test_non_image_answers_null_image_without_model_call(self):
        with patch("app.contextflow.vision.local_urlopen") as opened:
            response = self.image_suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["image"])
        opened.assert_not_called()

    def test_oversize_image_answers_400(self):
        storage = _StubStorage(b"", size=6 * 1024 * 1024)
        with (
            patch("app.contextflow.doc_tags.storage_for", return_value=storage),
            self.ollama([VISION_CHUNKS]),
        ):
            response = self.image_suggest(self.png_id)

        self.assertEqual(400, response.status_code)
        self.assertIn("5 MiB", response.get_json()["error"])
        self.assertEqual(0, storage.reads)

    def test_chat_body_streams_bounded_and_omits_think(self):
        with self.ollama([TEXT_CHUNKS]) as opened:
            response = self.suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        calls = _chat_requests(opened)
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].full_url.endswith("/api/chat"))
        bodies = _chat_bodies(opened)
        self.assertEqual(1, len(bodies))
        self.assertEqual(
            {"model", "messages", "stream", "format", "options"},
            set(bodies[0]),
        )
        self.assertIs(True, bodies[0]["stream"])
        schema = bodies[0]["format"]
        self.assertEqual(["tags"], schema["required"])
        self.assertEqual({"tag", "reason", "evidence"}, set(schema["properties"]["tags"]["items"]["required"]))
        self.assertEqual({"num_predict": TAG_NUM_PREDICT, "temperature": 0}, bodies[0]["options"])
        self.assertNotIn("think", bodies[0])
        self.assertNotIn("think", bodies[0]["options"])
        self.assertEqual(1, len(bodies[0]["messages"]))
        user = bodies[0]["messages"][0]
        self.assertEqual("user", user["role"])
        self.assertNotIn("images", user)
        self.assertTrue(user["content"])

    def test_tag_prompt_requires_few_grounded_tags(self):
        prompt = build_tag_prompt(
            filename="notizen.txt",
            tags=[],
            folder_hint="imports",
            text="Rechnung über 120 Euro.",
        )

        self.assertIn("höchstens 5", prompt)
        self.assertIn('"evidence"', prompt)
        self.assertIn("wörtliches Zitat", prompt)
        self.assertIn("keine Varianten", prompt)
        self.assertIn("kommt wörtlich", prompt)
        self.assertIn("keine Füllwörter", prompt)
        self.assertIn("Dateinamen-Floskeln", prompt)

    def test_image_instruction_limits_to_single_primary_object(self):
        self.assertIn("hoechstens EIN", IMAGE_INSTRUCTION)
        self.assertIn("keine Teile", IMAGE_INSTRUCTION)
        self.assertIn("keine Materialien", IMAGE_INSTRUCTION)
        self.assertIn("leere Liste", IMAGE_INSTRUCTION)
        self.assertIn("woertlich wiederholen", IMAGE_INSTRUCTION)

    def test_evidence_quote_must_match_source_text(self):
        source = "notizen.txt\nRechnung über 120 Euro aus dem Jahr 2024."
        matched = validate_tag_suggestions(
            {"tags": [{"tag": "Rechnung", "reason": "Belegt.", "evidence": "  RECHNUNG  über 120 EURO "}]},
            [],
            source_text=source,
        )

        self.assertEqual(
            [{"tag": "Rechnung", "reason": "Belegt.", "already": False,
              "evidence": "RECHNUNG  über 120 EURO"}],
            matched,
        )
        self.assertEqual(
            [],
            validate_tag_suggestions(
                {"tags": [{"tag": "Rechnung", "reason": "Belegt.", "evidence": "Spalte D"}]},
                [],
                source_text=source,
            ),
        )

    def test_evidence_edge_cases(self):
        source = "notizen.txt\nRechnung über 120 Euro."
        for evidence in ("x" * 161, '""', "   "):
            with self.subTest(evidence=evidence):
                self.assertEqual(
                    [],
                    validate_tag_suggestions(
                        {"tags": [{"tag": "Rechnung", "reason": "B.", "evidence": evidence}]},
                        [],
                        source_text=source,
                    ),
                )

    def test_missing_evidence_and_unverifiable_drop(self):
        self.assertEqual(
            [],
            validate_tag_suggestions(
                {"tags": [{"tag": "Rechnung", "reason": "B."}]},
                [],
                source_text="notizen.txt\nRechnung.",
            ),
        )
        self.assertEqual(
            [],
            validate_tag_suggestions(
                {"tags": [{"tag": "Rechnung", "reason": "B.", "evidence": "Rechnung"}]},
                [],
            ),
        )

    def test_suggestion_and_object_caps(self):
        many_tags = {
            "tags": [
                {"tag": f"Tag{i}", "reason": "B.", "evidence": "Tag"}
                for i in range(7)
            ]
        }
        capped = validate_tag_suggestions(
            many_tags, [], source_text="Tag0 Tag1 Tag2 Tag3 Tag4 Tag5 Tag6"
        )
        self.assertEqual(5, len(capped))
        self.assertEqual("Tag0", capped[0]["tag"])
        many_objects = {
            "description": "Eine Bank im Foto.",
            "objects": ["Bank", "Stuhl", "Tisch", "Lampe", "Regal", "Teppich"],
        }
        self.assertEqual(
            ["Bank"],
            validate_image_description(many_objects)["objects"],
        )

    def test_suggest_echoes_matching_evidence(self):
        with self.ollama([TEXT_EVIDENCE_CHUNKS]):
            response = self.suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        rows = response.get_json()["suggestions"]
        self.assertEqual("120 Euro", rows[0]["evidence"])

    def test_suggest_without_grounded_tags_answers_200_empty(self):
        before = DocumentStore(self.document_root).get_document(self.txt_id).get("tags", [])
        with self.ollama([TEXT_INVENTED_CHUNKS]):
            response = self.suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual([], payload["suggestions"])
        self.assertIn("provider_label", payload)
        self.assertIn("model", payload)
        after = DocumentStore(self.document_root).get_document(self.txt_id).get("tags", [])
        self.assertEqual(before, after)

    def test_tag_phrase_must_occur_in_source(self):
        source = "notizen.txt\nRechnung über 120 Euro aus dem Jahr 2024."
        out = validate_tag_suggestions(
            {"tags": [
                {"tag": "Rechnung", "reason": "B.", "evidence": "Rechnung über 120 Euro"},
                {"tag": "120_Euro", "reason": "B.", "evidence": "120 Euro"},
                {"tag": "Einkommen", "reason": "B.", "evidence": "Rechnung über 120 Euro"},
                {"tag": "kostenrechnung", "reason": "B.", "evidence": "Rechnung über 120 Euro"},
                {"tag": "Rechnung 2024", "reason": "B.", "evidence": "Jahr 2024"},
            ]},
            [],
            source_text=source,
        )

        self.assertEqual(["Rechnung", "120_Euro"], [row["tag"] for row in out])

    def test_stopword_tags_drop_but_content_keeps(self):
        out = validate_tag_suggestions(
            {"tags": [
                {"tag": "aus", "reason": "B.", "evidence": "Abholung aus dem Lager"},
                {"tag": "am", "reason": "B.", "evidence": "am Montag"},
                {"tag": "aus dem", "reason": "B.", "evidence": "aus dem Lager"},
                {"tag": "a", "reason": "B.", "evidence": "Abholung"},
                {"tag": "Lager", "reason": "B.", "evidence": "dem Lager"},
                {"tag": "Montag", "reason": "B.", "evidence": "am Montag"},
            ]},
            [],
            filename="bericht.txt",
            body_text="Abholung aus dem Lager am Montag.",
        )

        self.assertEqual(["Lager", "Montag"], [row["tag"] for row in out])

    def test_generic_filename_stems_drop_only_without_body_support(self):
        file_only = validate_tag_suggestions(
            {"tags": [
                {"tag": "notizen", "reason": "B.", "evidence": "notizen"},
                {"tag": "scan2", "reason": "B.", "evidence": "scan2"},
                {"tag": "Rechnung", "reason": "B.", "evidence": "Rechnung"},
            ]},
            [],
            filename="notizen_scan2.txt",
            body_text="Rechnung über 120 Euro.",
        )
        body_backed = validate_tag_suggestions(
            {"tags": [
                {"tag": "Notizen", "reason": "B.", "evidence": "Die Notizen"},
            ]},
            [],
            filename="bericht.txt",
            body_text="Die Notizen zum Treffen liegen bei.",
        )

        self.assertEqual(["Rechnung"], [row["tag"] for row in file_only])
        self.assertEqual(["Notizen"], [row["tag"] for row in body_backed])

    def test_component_material_attribute_labels_drop_from_objects(self):
        def objects_for(description, candidates):
            return validate_image_description(
                {"description": description, "objects": candidates}
            )["objects"]

        self.assertEqual(
            ["Stuhl"],
            objects_for("Ein Stuhl aus Holz.", ["Sitzfläche", "Stuhl"]),
        )
        self.assertEqual(
            [], objects_for("Ein Stuhl aus Holz.", ["Sitzfläche", "Stützen"])
        )
        self.assertEqual([], objects_for("Ein Tisch.", ["runde Platte"]))
        self.assertEqual(
            ["runder Tisch"], objects_for("Ein runder Tisch.", ["runder Tisch"])
        )
        self.assertEqual([], objects_for("Ein Regal.", ["Holz"]))
        self.assertEqual([], objects_for("Eine Platte.", ["die Platte"]))
        self.assertEqual([], objects_for("Unklar.", []))
        self.assertEqual(
            ["Die Bank"], objects_for("Die Bank steht im Park.", ["Die Bank"])
        )
        self.assertEqual(
            ["Kanne"], objects_for("Eine Kanne auf dem Tisch.", ["Kanne"])
        )
        self.assertEqual(
            ["Matte"], objects_for("Eine Matte liegt aus.", ["Matte"])
        )
        self.assertEqual(
            ["Glas"], objects_for("Ein Glas steht da.", ["Glas"])
        )
        self.assertEqual(
            ["Teller"], objects_for("Ein Teller.", ["Teller"])
        )
        uncertain = validate_image_description(
            {"description": "Unklar.", "objects": []}
        )
        self.assertEqual({"description": "Unklar.", "objects": []}, uncertain)

    def test_image_label_must_match_description(self):
        matched = validate_image_description(
            {"description": "Eine Bank im Park.", "objects": ["Bank"]}
        )
        self.assertEqual(["Bank"], matched["objects"])
        mismatched = validate_image_description(
            {
                "description": (
                    "Ein stilisiertes, einfaches Holzbrett mit vier Beinen."
                ),
                "objects": ["Bank"],
            }
        )
        self.assertEqual([], mismatched["objects"])
        self.assertEqual(
            "Ein stilisiertes, einfaches Holzbrett mit vier Beinen.",
            mismatched["description"],
        )
        component_first = validate_image_description(
            {
                "description": "Ein Stuhl aus Holz.",
                "objects": ["Sitzfläche", "Stuhl"],
            }
        )
        self.assertEqual(["Stuhl"], component_first["objects"])
        uncertain = validate_image_description(
            {"description": "Eine Bank im Park.", "objects": []}
        )
        self.assertEqual([], uncertain["objects"])

    def test_suggest_keeps_only_supported_tags(self):
        with self.ollama([TEXT_MIXED_CHUNKS]):
            response = self.suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        rows = response.get_json()["suggestions"]
        self.assertEqual(["Rechnung"], [row["tag"] for row in rows])

    def test_suggest_filters_weak_and_generic_tags(self):
        with self.ollama([TEXT_WEAK_CHUNKS]):
            response = self.suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        rows = response.get_json()["suggestions"]
        self.assertEqual(["Euro"], [row["tag"] for row in rows])

    def test_image_parts_only_answers_empty_objects_without_store(self):
        before = DocumentStore(self.document_root).get_document(self.png_id).get("tags", [])
        with self.ollama([VISION_PARTS_CHUNKS]):
            response = self.image_suggest(self.png_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("Ein Stuhl aus Holz.", payload["image"]["description"])
        self.assertEqual([], payload["image"]["objects"])
        self.assertIn("model", payload)
        after = DocumentStore(self.document_root).get_document(self.png_id).get("tags", [])
        self.assertEqual(before, after)

    def test_supported_meta_model_suggests_directly(self):
        app.config.update(CONTEXTFLOW_OLLAMA_MODEL="llama3.2-vision:11b")
        calls: list = []
        with self.ollama([TEXT_CHUNKS], calls=calls):
            response = self.suggest(self.txt_id)

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("llama3.2-vision:11b", payload["model"])
        self.assertEqual(["/api/show", "/api/chat"], calls)

    def test_missing_model_answers_503_with_pull_hint(self):
        app.config.update(CONTEXTFLOW_OLLAMA_MODEL="llama3.2-vision:11b")
        missing = urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/show", 404, "Not Found", {}, None
        )
        with self.ollama([], show=missing):
            response = self.suggest(self.txt_id)

        self.assertEqual(503, response.status_code)
        error = response.get_json()["error"]
        self.assertIn("nicht installiert", error)
        self.assertIn("ollama pull llama3.2-vision:11b", error)

    def test_timeout_returns_503_with_german_hint(self):
        with patch(
            "app.contextflow.vision.local_urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            response = self.suggest(self.txt_id)

        self.assertEqual(503, response.status_code)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("Zeitlimit", payload["error"])
        self.assertIs(True, payload.get("timeout"))

    def test_empty_response_returns_german_error(self):
        with self.ollama(
            [[{"message": {"role": "assistant", "content": "   "}, "done": True}]]
        ):
            response = self.suggest(self.txt_id)

        self.assertEqual(400, response.status_code)
        self.assertIn("leer geantwortet", response.get_json()["error"])

    def test_confirm_merges_new_and_skips_existing(self):
        store = DocumentStore(self.document_root)
        store.set_tags(self.txt_id, ["Rechnung"], "doc-engel-member")

        response = self.client.post(
            "/contextflow/doc-tag-confirm",
            data={
                "_csrf_token": CSRF_TOKEN,
                "document_id": self.txt_id,
                "tags": ["Rechnung", "Steuer"],
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(["Steuer"], payload["added"])
        self.assertEqual(["Rechnung"], payload["skipped"])
        persisted = DocumentStore(self.document_root).get_document(self.txt_id)["tags"]
        self.assertEqual(["Rechnung", "Steuer"], persisted)

    def test_confirm_rejects_invalid_tag_and_stores_nothing(self):
        before = DocumentStore(self.document_root).get_document(self.txt_id).get("tags", [])

        response = self.client.post(
            "/contextflow/doc-tag-confirm",
            data={"_csrf_token": CSRF_TOKEN, "document_id": self.txt_id, "tags": ["!!!"]},
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("Ungültiger Tag", response.get_json()["error"])
        after = DocumentStore(self.document_root).get_document(self.txt_id).get("tags", [])
        self.assertEqual(before, after)

    def test_unknown_document_is_404_on_suggest_and_confirm(self):
        with self.ollama([TEXT_CHUNKS]):
            suggested = self.suggest("missing-document")
        imaged = self.image_suggest("missing-document")
        confirmed = self.client.post(
            "/contextflow/doc-tag-confirm",
            data={"_csrf_token": CSRF_TOKEN, "document_id": "missing-document", "tags": ["x"]},
        )

        self.assertEqual(404, suggested.status_code)
        self.assertEqual(404, imaged.status_code)
        self.assertEqual(404, confirmed.status_code)

    def test_missing_model_returns_503_with_german_hint(self):
        app.config.update(CONTEXTFLOW_OLLAMA_MODEL="")

        with patch("app.contextflow.vision.local_urlopen") as opened:
            response = self.suggest(self.txt_id)

        self.assertEqual(503, response.status_code)
        self.assertIn("Meta-Modell", response.get_json()["error"])
        opened.assert_not_called()

    def test_unreachable_backend_returns_503(self):
        with patch("app.contextflow.vision.local_urlopen", side_effect=urllib.error.URLError("down")):
            response = self.suggest(self.txt_id)

        self.assertEqual(503, response.status_code)
        self.assertIn("nicht erreichbar", response.get_json()["error"])

    def test_confirm_needs_no_model(self):
        app.config.update(CONTEXTFLOW_OLLAMA_MODEL="")

        response = self.client.post(
            "/contextflow/doc-tag-confirm",
            data={"_csrf_token": CSRF_TOKEN, "document_id": self.txt_id, "tags": ["Ablage"]},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(["Ablage"], response.get_json()["added"])

    def test_routes_need_flag_and_documents_feature(self):
        app.config.update(CONTEXTFLOW_ENABLED=False)
        flagged_off = self.suggest(self.txt_id)
        image_flagged_off = self.image_suggest(self.txt_id)
        app.config.update(CONTEXTFLOW_ENABLED=True)
        with app.app_context():
            db = database.get_db()
            db.execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at) VALUES(?,'documents',0,?)",
                (self.user_id, "2026-10-28T00:00:00+00:00"),
            )
            db.commit()

        suggest_denied = self.suggest(self.txt_id)
        image_denied = self.image_suggest(self.txt_id)
        confirm_denied = self.client.post(
            "/contextflow/doc-tag-confirm",
            data={"_csrf_token": CSRF_TOKEN, "document_id": self.txt_id, "tags": ["x"]},
        )

        self.assertEqual(404, flagged_off.status_code)
        self.assertEqual(404, image_flagged_off.status_code)
        self.assertEqual(403, suggest_denied.status_code)
        self.assertEqual(403, image_denied.status_code)
        self.assertEqual(403, confirm_denied.status_code)

    def test_validators_skip_invalid_entries_and_flag_existing(self):
        suggestions = validate_tag_suggestions(
            {
                "tags": [
                    {"tag": "Rechnung", "reason": "Belegt.", "evidence": "Rechnung"},
                    {"tag": "!!!", "reason": "Schrott."},
                    {"tag": "rechnung", "reason": "Doppelt."},
                    {"tag": "", "reason": "Leer."},
                ]
            },
            ["Rechnung"],
            source_text="ablage.txt\nRechnung vom Markt.",
        )
        image = validate_image_description(
            {"description": "Eine Bank im Foto.", "objects": ["Bank", "", "x" * 41]}
        )

        self.assertEqual(
            [{"tag": "Rechnung", "reason": "Belegt.", "already": True,
              "evidence": "Rechnung"}],
            suggestions,
        )
        self.assertEqual({"description": "Eine Bank im Foto.", "objects": ["Bank"]}, image)
        self.assertEqual([], validate_tag_suggestions({"tags": [{"tag": "!!!"}]}, []))
        with self.assertRaises(ValueError):
            validate_tag_suggestions({"tags": "keine-liste"}, [])
        with self.assertRaises(ValueError):
            validate_image_description({"description": "", "objects": []})

    def test_image_mime_is_derived_from_validated_suffix(self) -> None:
        self.assertEqual("image/png", image_mime_type("foto.png"))
        self.assertEqual("image/jpeg", image_mime_type("foto.jpg"))
        self.assertEqual("image/jpeg", image_mime_type("FOTO.JPEG"))
        self.assertEqual("image/webp", image_mime_type("foto.webp"))
        self.assertEqual("", image_mime_type("foto.gif"))


class DocTagMetaCloudRefusal(unittest.TestCase):
    """Document suggestions never reach the Meta cloud client.

    Both suggestion entries take no consent argument and select
    their backend via ``_require_local_provider`` (per-request
    consent hardcoded ``False``), so even a fully configured Meta
    setup — provider, server consent, key, endpoint, model — must
    fail closed before any transport is touched.
    """

    def _full_consent_meta_settings(self) -> dict:
        return {
            "provider": "meta",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2:1b",
            "ollama_timeout": 60,
            "meta_consent": True,
            "meta_api_key": "test-key-not-a-secret",
            "meta_endpoint": META_MODEL_API_ENDPOINT,
            "meta_model": META_MODEL_API_MODEL,
        }

    def test_text_tags_refuse_meta_cloud_even_with_full_consent(self) -> None:
        document = {"document_id": "doc-1", "last_path": "notizen.txt", "tags": []}
        with (
            patch("app.contextflow.vision.local_urlopen") as local_opened,
            patch(
                "app.contextflow.vision.MetaVisionProvider._post_response"
            ) as cloud,
        ):
            with self.assertRaises(PermissionError):
                suggest_tags_for_document(
                    document=document,
                    settings=self._full_consent_meta_settings(),
                    root="/tmp/none",
                    actor="anna",
                )
        local_opened.assert_not_called()
        cloud.assert_not_called()

    def test_image_description_refuses_meta_cloud_even_with_full_consent(
        self,
    ) -> None:
        document = {"document_id": "doc-1", "last_path": "foto.png"}
        with (
            patch("app.contextflow.vision.local_urlopen") as local_opened,
            patch(
                "app.contextflow.vision.MetaVisionProvider._post_response"
            ) as cloud,
        ):
            with self.assertRaises(PermissionError):
                suggest_image_for_document(
                    document=document,
                    settings=self._full_consent_meta_settings(),
                    root="/tmp/none",
                    actor="anna",
                )
        local_opened.assert_not_called()
        cloud.assert_not_called()


if __name__ == "__main__":
    unittest.main()
