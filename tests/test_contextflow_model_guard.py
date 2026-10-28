"""Meta-only Ollama model gates and bounded model probing tests."""
from __future__ import annotations

import json
import unittest
import urllib.error
from unittest import mock

from app.contextflow.vision import InferenceUnavailable, VisionError, suggest_inventory_object
from tests.contextflow_ollama_fakes import SHOW_PLAIN, SHOW_THINKING, _chat_bodies, _local_route


class ModelGuardTest(unittest.TestCase):
    def _settings(self, model: str) -> dict:
        return {
            "provider": "local",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": model,
        }

    def _hammer_chat(self) -> list:
        answer = json.dumps(
            {"object_name": "Hammer", "item_type": "tool", "confidence": 90, "note": ""}
        )
        return [{"message": {"role": "assistant", "content": answer}, "done": True}]

    def test_approved_meta_vision_model_runs_directly(self) -> None:
        calls: list = []
        route = _local_route(show=SHOW_PLAIN, chat=self._hammer_chat(), calls=calls)
        with mock.patch("app.contextflow.vision.local_urlopen", side_effect=route) as opener:
            out = suggest_inventory_object(
                b"x", "image/png", {}, self._settings("llama3.2-vision:11b")
            )
        self.assertEqual("Hammer", out["suggestion"]["object_name"])
        self.assertEqual("llama3.2-vision:11b", out["model"])
        self.assertEqual(["/api/show", "/api/chat"], calls)
        self.assertEqual("llama3.2-vision:11b", _chat_bodies(opener)[0]["model"])

    def test_non_meta_model_is_refused_before_network(self) -> None:
        with mock.patch("app.contextflow.vision.local_urlopen") as transport:
            with self.assertRaises(VisionError) as raised:
                suggest_inventory_object(
                    b"x", "image/png", {}, self._settings("qwen3-vl:2b")
                )
        self.assertIn("ausschließlich Meta-Modelle", str(raised.exception))
        transport.assert_not_called()

    def test_text_only_meta_model_is_refused_for_image_tasks(self) -> None:
        with mock.patch("app.contextflow.vision.local_urlopen") as transport:
            with self.assertRaises(VisionError) as raised:
                suggest_inventory_object(
                    b"x", "image/png", {}, self._settings("llama3.2:1b")
                )
        self.assertIn("nur Text", str(raised.exception))
        transport.assert_not_called()

    def test_allowed_meta_model_is_not_rejected_for_thinking_capability(self) -> None:
        calls: list = []
        route = _local_route(
            show=SHOW_THINKING, chat=self._hammer_chat(), calls=calls
        )
        with mock.patch("app.contextflow.vision.local_urlopen", side_effect=route) as opener:
            out = suggest_inventory_object(
                b"x", "image/png", {}, self._settings("muse-glimmer")
            )
        self.assertEqual("Hammer", out["suggestion"]["object_name"])
        self.assertEqual(["/api/show", "/api/chat"], calls)
        self.assertEqual("muse-glimmer", _chat_bodies(opener)[0]["model"])

    def test_missing_model_reports_pull_hint(self) -> None:
        calls: list = []
        route = _local_route(
            show=urllib.error.HTTPError(
                "http://127.0.0.1:11434/api/show", 404, "Not Found", {}, None
            ),
            calls=calls,
        )
        with mock.patch("app.contextflow.vision.local_urlopen", side_effect=route):
            with self.assertRaises(InferenceUnavailable) as raised:
                suggest_inventory_object(
                    b"x", "image/png", {}, self._settings("llama3.2-vision:11b")
                )
        self.assertIn("nicht installiert", str(raised.exception))
        self.assertIn("ollama pull llama3.2-vision:11b", str(raised.exception))
        self.assertEqual(["/api/show"], calls)

    def test_show_probe_failure_fails_open_for_approved_model(self) -> None:
        calls: list = []
        route = _local_route(
            show=urllib.error.URLError("show down"),
            chat=self._hammer_chat(),
            calls=calls,
        )
        with mock.patch("app.contextflow.vision.local_urlopen", side_effect=route) as opener:
            out = suggest_inventory_object(
                b"x", "image/png", {}, self._settings("muse-glimmer")
            )
        self.assertEqual("Hammer", out["suggestion"]["object_name"])
        self.assertEqual("muse-glimmer", out["model"])
        self.assertEqual("muse-glimmer", _chat_bodies(opener)[0]["model"])
        self.assertEqual(["/api/show", "/api/chat"], calls)

if __name__ == "__main__":
    unittest.main()
