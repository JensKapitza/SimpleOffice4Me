"""Doc-tag schema regression: string answers, unbacked and grounded tags.

Live case (synthetic data only): ``doc_tags.complete_text()`` with the
approved Ollama model ``llama3.2:1b`` answered with bare strings for
``Wartung-Waermepumpe-2026.pdf``; strict validation dropped all of
them, so the user got no usable suggestions. The request now carries
an Ollama JSON Schema (object shape enforced model-side), while
evidence truth still rests solely on the unchanged
``validate_tag_suggestions()`` — invented quotes never surface.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.contextflow.doc_tags import (
    TAG_JSON_SCHEMA,
    TAG_NUM_PREDICT,
    TAG_TEMPERATURE,
    build_tag_prompt,
    complete_text,
    suggest_tags_for_document,
    validate_tag_suggestions,
)
from app.contextflow.vision import ollama_chat_body

LIVE_FILENAME = "Wartung-Waermepumpe-2026.pdf"
LIVE_BODY = (
    "Rechnung für Wartung der Wärmepumpe. Hersteller Bosch. "
    "Prüftermin am 30.10.2026."
)


class _DummyResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class DocTagSchemaRegression(unittest.TestCase):
    def validate(self, raw):
        return validate_tag_suggestions(
            raw, [], filename=LIVE_FILENAME, body_text=LIVE_BODY
        )

    def test_string_entries_drop_while_grounded_object_survives(self):
        out = self.validate(
            {
                "tags": [
                    "Hersteller: Bosch",
                    "Produkt: Wärmepumpe",
                    "Prüftermin: 30.10.2026",
                    {
                        "tag": "Bosch",
                        "reason": "Der Text nennt Bosch als Hersteller.",
                        "evidence": "Hersteller Bosch",
                    },
                ]
            }
        )

        self.assertEqual(["Bosch"], [row["tag"] for row in out])
        self.assertEqual("Hersteller Bosch", out[0]["evidence"])

    def test_unbacked_live_case_yields_empty_without_invention(self):
        out = self.validate(
            {
                "tags": [
                    "Hersteller: Bosch",
                    {
                        "tag": "Wärmepumpe",
                        "reason": "Vertragsdaten.",
                        "evidence": "Anzeige bei der Bosch GmbH",
                    },
                    {
                        "tag": "30.10.2026",
                        "reason": "Vertragsdaten.",
                        "evidence": "Datum der Anzeige",
                    },
                ]
            }
        )

        self.assertEqual([], out)

    def test_grounded_objects_survive_with_evidence_echo(self):
        out = self.validate(
            {
                "tags": [
                    {
                        "tag": "Bosch",
                        "reason": "Der Text nennt Bosch als Hersteller.",
                        "evidence": "Hersteller Bosch",
                    },
                    {
                        "tag": "Prüftermin",
                        "reason": "Der Text nennt einen Prüftermin.",
                        "evidence": "Prüftermin am 30.10.2026",
                    },
                ]
            }
        )

        self.assertEqual(["Bosch", "Prüftermin"], [row["tag"] for row in out])
        self.assertEqual("Hersteller Bosch", out[0]["evidence"])
        self.assertEqual("Prüftermin am 30.10.2026", out[1]["evidence"])

    def test_prompt_demands_object_entries(self):
        prompt = build_tag_prompt(
            filename=LIVE_FILENAME, tags=[], folder_hint="", text=LIVE_BODY
        )

        self.assertIn("genau den Schlüsseln tag, reason und evidence", prompt)
        self.assertIn("niemals nur", prompt)

    def test_complete_text_sends_schema_and_accepts_fenced_json(self):
        provider = SimpleNamespace(
            base_url="http://127.0.0.1:11434", model="llama3.2:1b", timeout=60
        )
        fenced = '```json\n{"tags": [{"tag": "Bosch", "reason": "B.", "evidence": "E."}]}\n```'
        with (
            patch(
                "app.contextflow.doc_tags.vision.resolve_local_model",
                return_value="llama3.2:1b",
            ),
            patch(
                "app.contextflow.doc_tags.vision.local_urlopen",
                return_value=_DummyResponse(),
            ) as opened,
            patch(
                "app.contextflow.doc_tags.vision.parse_chat_stream",
                return_value=fenced,
            ),
        ):
            parsed = complete_text(provider, "Welche Tags?", json_schema=TAG_JSON_SCHEMA)

        self.assertEqual("llama3.2:1b", provider.resolved_model)
        self.assertEqual("Bosch", parsed["tags"][0]["tag"])
        sent = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(TAG_JSON_SCHEMA, sent["format"])
        self.assertIs(True, sent["stream"])

    def test_suggest_wires_schema_and_token_budget(self):
        document = {
            "document_id": "live-case",
            "last_path": LIVE_FILENAME,
            "extracted_text": LIVE_BODY,
            "tags": [],
        }
        settings = {
            "provider": "local",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2:1b",
            "ollama_timeout": 60,
        }
        with patch(
            "app.contextflow.doc_tags.complete_text", return_value={"tags": []}
        ) as completed:
            out = suggest_tags_for_document(
                document=document, settings=settings, root="/tmp", actor="member"
            )

        self.assertEqual([], out["suggestions"])
        _, kwargs = completed.call_args
        self.assertEqual(TAG_JSON_SCHEMA, kwargs["json_schema"])
        self.assertEqual(TAG_NUM_PREDICT, kwargs["num_predict"])
        self.assertEqual(TAG_TEMPERATURE, kwargs["temperature"])
        self.assertEqual(0, TAG_TEMPERATURE)
        self.assertEqual("llama3.2:1b", out["model"])

    def test_only_tag_path_pins_temperature_zero(self):
        default = json.loads(
            ollama_chat_body(model="llama3.2:1b", messages=[]).decode("utf-8")
        )
        pinned = json.loads(
            ollama_chat_body(
                model="llama3.2:1b", messages=[], temperature=TAG_TEMPERATURE
            ).decode("utf-8")
        )

        self.assertEqual({"num_predict": 2048}, default["options"])
        self.assertNotIn("temperature", default["options"])
        self.assertEqual(0, pinned["options"]["temperature"])
        self.assertEqual(2048, pinned["options"]["num_predict"])

    def test_complete_text_defaults_to_plain_json_for_other_callers(self):
        provider = SimpleNamespace(
            base_url="http://127.0.0.1:11434", model="llama3.2:1b", timeout=60
        )
        with (
            patch(
                "app.contextflow.doc_tags.vision.resolve_local_model",
                return_value="llama3.2:1b",
            ),
            patch(
                "app.contextflow.doc_tags.vision.local_urlopen",
                return_value=_DummyResponse(),
            ) as opened,
            patch(
                "app.contextflow.doc_tags.vision.parse_chat_stream",
                return_value='{"tags": []}',
            ),
        ):
            parsed = complete_text(provider, "Welche Tags?")

        self.assertEqual({"tags": []}, parsed)
        sent = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertEqual("json", sent["format"])
        self.assertEqual({"num_predict": 2048}, sent["options"])

    def test_complete_text_forwards_pinned_temperature(self):
        provider = SimpleNamespace(
            base_url="http://127.0.0.1:11434", model="llama3.2:1b", timeout=60
        )
        with (
            patch(
                "app.contextflow.doc_tags.vision.resolve_local_model",
                return_value="llama3.2:1b",
            ),
            patch(
                "app.contextflow.doc_tags.vision.local_urlopen",
                return_value=_DummyResponse(),
            ) as opened,
            patch(
                "app.contextflow.doc_tags.vision.parse_chat_stream",
                return_value='{"tags": []}',
            ),
        ):
            complete_text(
                provider,
                "Welche Tags?",
                json_schema=TAG_JSON_SCHEMA,
                num_predict=TAG_NUM_PREDICT,
                temperature=TAG_TEMPERATURE,
            )

        sent = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(
            {"num_predict": TAG_NUM_PREDICT, "temperature": 0}, sent["options"]
        )


if __name__ == "__main__":
    unittest.main()
