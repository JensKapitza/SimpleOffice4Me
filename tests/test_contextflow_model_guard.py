"""ContextFlow model guard: instruct routing and thinking refusal.

A thinking model streams reasoning instead of answers, so the local
path resolves the effective model first: an installed `-instruct`
sibling is used, otherwise a fast actionable setup error is raised
and the thinking model is never run. No network, no downloads.
"""
from __future__ import annotations

import json
import unittest
import urllib.error
from unittest import mock

from app.contextflow.vision import InferenceUnavailable, suggest_inventory_object
from tests.contextflow_ollama_fakes import (
    SHOW_PLAIN,
    SHOW_THINKING,
    _chat_bodies,
    _local_route,
)


class ModelGuardTest(unittest.TestCase):
    def _settings(self, model: str) -> dict:
        return {
            "provider": "local",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": model,
        }

    def _hammer_chat(self) -> list:
        answer = json.dumps(
            {"object_name": "Hammer", "item_type": "tool",
             "confidence": 90, "note": ""}
        )
        return [{"message": {"role": "assistant", "content": answer}, "done": True}]

    def test_instruct_model_used_directly_without_tags_lookup(self) -> None:
        for show in (
            {"capabilities": ["completion", "vision"]},
            {"capabilities": ["completion", "vision", "thinking"]},
        ):
            with self.subTest(show=show):
                calls: list = []
                route = _local_route(
                    show=show, chat=self._hammer_chat(), calls=calls
                )
                with mock.patch(
                    "app.contextflow.vision.local_urlopen", side_effect=route
                ) as opener:
                    out = suggest_inventory_object(
                        b"x", "image/png", {},
                        self._settings("qwen3-vl:2b-instruct"),
                    )
                self.assertEqual("Hammer", out["suggestion"]["object_name"])
                self.assertEqual("qwen3-vl:2b-instruct", out["model"])
                self.assertEqual(["/api/show", "/api/chat"], calls)
                bodies = _chat_bodies(opener)
                self.assertEqual("qwen3-vl:2b-instruct", bodies[0]["model"])

    def test_thinking_model_uses_installed_instruct_sibling(self) -> None:
        calls: list = []
        route = _local_route(
            show=SHOW_THINKING,
            tags={"models": [{"name": "qwen3-vl:2b-instruct"}, {"name": "other:7b"}]},
            chat=self._hammer_chat(),
            calls=calls,
        )
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            out = suggest_inventory_object(
                b"x", "image/png", {}, self._settings("qwen3-vl:2b")
            )
        self.assertEqual("Hammer", out["suggestion"]["object_name"])
        self.assertEqual("qwen3-vl:2b-instruct", out["model"])
        bodies = _chat_bodies(opener)
        self.assertEqual("qwen3-vl:2b-instruct", bodies[0]["model"])
        self.assertEqual(["/api/show", "/api/tags", "/api/chat"], calls)

    def test_thinking_model_without_sibling_refuses_fast(self) -> None:
        calls: list = []
        route = _local_route(
            show=SHOW_THINKING, tags={"models": []}, calls=calls
        )
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ):
            with self.assertRaises(InferenceUnavailable) as raised:
                suggest_inventory_object(
                    b"x", "image/png", {}, self._settings("qwen3-vl:2b")
                )
        message = str(raised.exception)
        self.assertIn("qwen3-vl:2b", message)
        self.assertIn("ollama pull qwen3-vl:2b-instruct", message)
        self.assertIn("CONTEXTFLOW_OLLAMA_MODEL=qwen3-vl:2b-instruct", message)
        self.assertIn("automatisch", message)
        self.assertEqual(["/api/show", "/api/tags"], calls)

    def test_thinking_model_with_failing_tags_refuses(self) -> None:
        calls: list = []
        route = _local_route(
            show=SHOW_THINKING,
            tags=urllib.error.URLError("tags down"),
            calls=calls,
        )
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ):
            with self.assertRaises(InferenceUnavailable) as raised:
                suggest_inventory_object(
                    b"x", "image/png", {}, self._settings("qwen3-vl:2b")
                )
        self.assertIn("ollama pull qwen3-vl:2b-instruct", str(raised.exception))
        self.assertNotIn("/api/chat", calls)

    def test_missing_model_reports_pull_hint(self) -> None:
        calls: list = []
        route = _local_route(
            show=urllib.error.HTTPError(
                "http://127.0.0.1:11434/api/show", 404, "Not Found", {}, None
            ),
            calls=calls,
        )
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ):
            with self.assertRaises(InferenceUnavailable) as raised:
                suggest_inventory_object(
                    b"x", "image/png", {}, self._settings("qwen3-vl:2b-instruct")
                )
        message = str(raised.exception)
        self.assertIn("nicht installiert", message)
        self.assertIn("ollama pull qwen3-vl:2b-instruct", message)
        self.assertEqual(["/api/show"], calls)

    def test_show_probe_failure_fails_open(self) -> None:
        calls: list = []
        route = _local_route(
            show=urllib.error.URLError("show down"),
            chat=self._hammer_chat(),
            calls=calls,
        )
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ) as opener:
            out = suggest_inventory_object(
                b"x", "image/png", {}, self._settings("modell")
            )
        self.assertEqual("Hammer", out["suggestion"]["object_name"])
        self.assertEqual("modell", out["model"])
        self.assertEqual("modell", _chat_bodies(opener)[0]["model"])
        self.assertEqual(["/api/show", "/api/chat"], calls)

    def test_plain_model_skips_tags_lookup(self) -> None:
        calls: list = []
        route = _local_route(
            show=SHOW_PLAIN, chat=self._hammer_chat(), calls=calls
        )
        with mock.patch(
            "app.contextflow.vision.local_urlopen", side_effect=route
        ):
            out = suggest_inventory_object(
                b"x", "image/png", {}, self._settings("modell")
            )
        self.assertEqual("modell", out["model"])
        self.assertEqual(["/api/show", "/api/chat"], calls)

    def test_renderer_and_case_signals_mark_thinking(self) -> None:
        for show in (
            {"capabilities": ["Completion", "THINKING"]},
            {"capabilities": ["completion"],
             "template": "{{ .Prompt }} qwen3-vl-thinking"},
            {"capabilities": ["completion"], "renderer": "qwen3-vl-thinking"},
        ):
            with self.subTest(show=show):
                calls: list = []
                route = _local_route(
                    show=show, tags={"models": []}, calls=calls
                )
                with mock.patch(
                    "app.contextflow.vision.local_urlopen", side_effect=route
                ):
                    with self.assertRaises(InferenceUnavailable):
                        suggest_inventory_object(
                            b"x", "image/png", {}, self._settings("qwen3-vl:2b")
                        )
                self.assertNotIn("/api/chat", calls)


if __name__ == "__main__":
    unittest.main()
