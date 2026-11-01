"""Meta Model API wiring: Responses transport, /v1/models check, no leaks."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from app.contextflow.vision import (
    META_MODEL_API_ENDPOINT,
    META_MODEL_API_MODEL,
    MetaVisionProvider,
    VisionError,
    check_meta_model,
    ml_status,
)


def _transport(payload: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = payload
    context = mock.MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    transport = mock.MagicMock()
    transport.open.return_value = context
    return transport


def _responses_bytes(text: str, status: str = "completed") -> bytes:
    return json.dumps(
        {
            "object": "response",
            "status": status,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        }
    ).encode("utf-8")


class MetaModelsTests(unittest.TestCase):
    def test_pins_point_at_responses_and_spark_contributor(self) -> None:
        self.assertEqual(
            "https://api.meta.ai/v1/responses", META_MODEL_API_ENDPOINT
        )
        self.assertEqual("muse-spark-1.3-contributor", META_MODEL_API_MODEL)

    def test_listed_model_returns_true(self) -> None:
        payload = json.dumps(
            {"object": "list", "data": [{"id": "muse-spark-1.3-contributor"}]}
        ).encode("utf-8")
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=_transport(payload),
        ):
            self.assertEqual((True, ""), check_meta_model("k"))

    def test_missing_model_reports_plain_reason(self) -> None:
        payload = json.dumps(
            {"object": "list", "data": [{"id": "other-model"}]}
        ).encode("utf-8")
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=_transport(payload),
        ):
            listed, error = check_meta_model("k")
        self.assertFalse(listed)
        self.assertIn("nicht gelistet", error)

    def test_transport_failure_never_leaks_key(self) -> None:
        secret = "test-only-dummy-meta-key-never-real-01"
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            side_effect=urllib.error.URLError("down"),
        ):
            listed, error = check_meta_model(secret)
        self.assertFalse(listed)
        self.assertNotIn(secret, error)
        listed, error = check_meta_model("")
        self.assertFalse(listed)
        self.assertIn("kein API-Schlüssel", error)

    def test_ml_status_probes_models_only_when_allowed(self) -> None:
        base = {
            "provider": "meta",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "",
            "meta_api_key": "k",
            "meta_consent": True,
        }
        with mock.patch(
            "app.contextflow.vision.check_meta_model", return_value=(True, "")
        ) as check:
            status = ml_status(dict(base), models_probe_allowed=True)
        check.assert_called_once_with("k")
        self.assertTrue(status["meta"]["model_listed"])
        self.assertEqual(status["meta"]["model"], "muse-spark-1.3-contributor")
        with mock.patch(
            "app.contextflow.vision.check_meta_model"
        ) as check:
            status = ml_status({**base, "provider": "local"})
            self.assertIsNone(status["meta"]["model_listed"])
            status = ml_status({**base, "meta_api_key": ""})
            self.assertIsNone(status["meta"]["model_listed"])
        check.assert_not_called()

    def test_ml_status_models_probe_denied_fail_closed(self) -> None:
        base = {
            "provider": "meta",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "",
            "meta_api_key": "k",
            "meta_consent": True,
        }
        with mock.patch(
            "app.contextflow.vision.check_meta_model"
        ) as check:
            status = ml_status(dict(base))
            self.assertIsNone(status["meta"]["model_listed"])
            self.assertIn("nicht freigegeben", status["meta"]["models_error"])
            status = ml_status(dict(base), models_probe_allowed=False)
            self.assertIsNone(status["meta"]["model_listed"])
        check.assert_not_called()

    def test_status_allows_text_model_and_keeps_spark_intact(self) -> None:
        tags = mock.MagicMock()
        tags.read.return_value = json.dumps(
            {"models": [{"name": "llama3.2:1b"}]}
        ).encode("utf-8")
        context = mock.MagicMock()
        context.__enter__.return_value = tags
        context.__exit__.return_value = False
        with mock.patch(
            "app.contextflow.vision.local_urlopen", return_value=context
        ) as probed:
            status = ml_status(
                {
                    "provider": "local",
                    "ollama_url": "http://127.0.0.1:11434",
                    "ollama_model": "llama3.2:1b",
                }
            )
        probed.assert_called_once()
        self.assertTrue(status["ollama"]["model_allowed"])
        self.assertTrue(status["ollama"]["model_seen"])
        with (
            mock.patch("app.contextflow.vision.local_urlopen") as probed,
            mock.patch(
                "app.contextflow.vision.check_meta_model",
                return_value=(True, ""),
            ),
        ):
            status = ml_status(
                {
                    "provider": "meta",
                    "ollama_url": "http://127.0.0.1:11434",
                    "ollama_model": "qwen3-vl:2b",
                    "meta_api_key": "k",
                    "meta_consent": True,
                },
                models_probe_allowed=True,
            )
        probed.assert_not_called()
        self.assertFalse(status["ollama"]["model_allowed"])
        self.assertTrue(status["meta"]["model_listed"])
        self.assertEqual(status["meta"]["model"], "muse-spark-1.3-contributor")

    def test_incomplete_response_fails_closed(self) -> None:
        provider = MetaVisionProvider(
            "k", META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL, text_only=True
        )
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=_transport(_responses_bytes("{}", status="failed")),
        ):
            with self.assertRaises(VisionError) as raised:
                provider.complete_text("hi")
        self.assertIn("did not complete", str(raised.exception))

    def test_missing_output_text_fails_closed(self) -> None:
        provider = MetaVisionProvider(
            "k", META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL, text_only=True
        )
        payload = json.dumps(
            {"object": "response", "status": "completed", "output": []}
        ).encode("utf-8")
        with mock.patch(
            "app.contextflow.vision.urllib.request.build_opener",
            return_value=_transport(payload),
        ):
            with self.assertRaises(VisionError):
                provider.complete_text("hi")


if __name__ == "__main__":
    unittest.main()
