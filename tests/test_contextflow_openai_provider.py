"""OpenAI cloud provider routing, gating and transport tests.

No network calls: the Chat Completions transport is faked by patching
``build_opener`` exactly like the Meta provider tests. The API key
travels only in the Authorization header and never appears in errors.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.contextflow.demo import DEMO_MARKER
from app.contextflow.openai_provider import (
    OPENAI_API_ENDPOINT,
    OpenAIVisionProvider,
    require_openai_model,
)
from app.contextflow.vision import (
    VisionError,
    ml_status,
    select_provider,
)


def _demo_root(base: Path) -> Path:
    demo = base / "demo-root"
    demo.mkdir()
    (demo / DEMO_MARKER).write_text(
        json.dumps({"synthetic": True, "tool": "contextflow_demo"}),
        encoding="utf-8",
    )
    return demo


def _transport(payload: bytes):
    response = mock.MagicMock()
    response.read.return_value = payload
    context = mock.MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    transport = mock.MagicMock()
    transport.open.return_value = context
    return transport


def _chat_payload(suggestion: dict) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(suggestion),
                    },
                    "finish_reason": "stop",
                }
            ],
        }
    ).encode("utf-8")


class OpenAIProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.demo = _demo_root(self.base)
        self.real = self.base / "real-root"
        self.real.mkdir()
        self.settings = {
            "provider": "openai",
            "openai_consent": True,
            "openai_api_key": "sk-test-key",
            "openai_endpoint": "",
            "openai_model": "",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_require_openai_model_allowlist(self) -> None:
        self.assertEqual("gpt-4o-mini", require_openai_model("gpt-4o-mini"))
        self.assertEqual("gpt-4o", require_openai_model("GPT-4O"))
        for bad in ("", "gpt-4", "o1", "llama3.2:1b", "gpt-4o-mini;rm"):
            with self.assertRaises(VisionError, msg=bad):
                require_openai_model(bad)

    def test_constructor_guards(self) -> None:
        with self.assertRaises(PermissionError):
            OpenAIVisionProvider("", OPENAI_API_ENDPOINT, "gpt-4o-mini")
        with self.assertRaises(VisionError):
            OpenAIVisionProvider("k", "https://example.com/v1", "gpt-4o-mini")
        with self.assertRaises(VisionError):
            OpenAIVisionProvider("k", OPENAI_API_ENDPOINT, "gpt-3.5-turbo")

    def test_select_provider_gates(self) -> None:
        provider = select_provider(dict(self.settings), True, self.demo)
        self.assertIsInstance(provider, OpenAIVisionProvider)
        self.assertEqual("gpt-4o-mini", provider.model)
        denied = dict(self.settings, openai_consent=False)
        with self.assertRaises(PermissionError):
            select_provider(denied, True, self.demo)
        with self.assertRaises(PermissionError):
            select_provider(dict(self.settings), False, self.demo)
        with self.assertRaises(PermissionError):
            select_provider(dict(self.settings, openai_api_key=""), True, self.demo)
        with self.assertRaises(PermissionError):
            select_provider(dict(self.settings), True, self.real)

    def test_suggest_posts_chat_completions(self) -> None:
        suggestion = {"title": "T", "description": "D",
                      "evidence_class": "observed", "confidence": 50}
        transport = _transport(_chat_payload(suggestion))
        with mock.patch(
            "app.contextflow.openai_provider.urllib.request.build_opener",
            return_value=transport,
        ):
            out = OpenAIVisionProvider(
                "sk-test-key", OPENAI_API_ENDPOINT, "gpt-4o-mini"
            ).suggest(b"x", "image/png", {})
        self.assertEqual(out, suggestion)
        sent = transport.open.call_args[0][0]
        self.assertEqual(
            sent.full_url, OPENAI_API_ENDPOINT + "/chat/completions"
        )
        self.assertEqual(sent.get_header("Authorization"), "Bearer sk-test-key")
        body = json.loads(sent.data.decode("utf-8"))
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        content = body["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(
            content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    def test_complete_text_roundtrip(self) -> None:
        answer = {"contact": "Lena Hoffmann"}
        transport = _transport(_chat_payload(answer))
        with mock.patch(
            "app.contextflow.openai_provider.urllib.request.build_opener",
            return_value=transport,
        ):
            out = OpenAIVisionProvider(
                "sk-test-key", OPENAI_API_ENDPOINT, "gpt-4o"
            ).complete_text("Wer?")
        self.assertEqual(out, answer)

    def test_error_payload_hides_key(self) -> None:
        payload = json.dumps(
            {"error": {"message": "bad key", "type": "invalid_request_error"}}
        ).encode("utf-8")
        transport = _transport(payload)
        with mock.patch(
            "app.contextflow.openai_provider.urllib.request.build_opener",
            return_value=transport,
        ):
            with self.assertRaises(VisionError) as raised:
                OpenAIVisionProvider(
                    "sk-test-key", OPENAI_API_ENDPOINT, "gpt-4o-mini"
                ).complete_text("Wer?")
        self.assertNotIn("sk-test-key", str(raised.exception))

    def test_ml_status_openai_section(self) -> None:
        status = ml_status(dict(self.settings))
        self.assertEqual(status["provider"], "openai")
        self.assertTrue(status["openai"]["key_present"])
        self.assertTrue(status["openai"]["server_consent"])
        self.assertIsNone(status["openai"]["model_listed"])


class OpenAIConfigTests(unittest.TestCase):
    def test_validate_accepts_openai_provider(self) -> None:
        from app import vision_config as config

        clean = config.validate(
            {"provider": "openai", "openai_consent": True,
             "openai_model": "gpt-4o-mini"}
        )
        self.assertEqual("openai", clean["provider"])
        self.assertTrue(clean["openai_consent"])
        with self.assertRaises(ValueError):
            config.validate({"provider": "chatgpt"})
        with self.assertRaises(ValueError):
            config.validate(
                {"provider": "openai",
                 "openai_endpoint": "https://example.com/v1"}
            )


if __name__ == "__main__":
    unittest.main()
