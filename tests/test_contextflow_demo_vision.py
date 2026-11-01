"""Focused start-config tests for Meta-only demo inference backends."""

from __future__ import annotations

import socket
import unittest
from unittest import mock

from tools.contextflow_demo import (
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_OLLAMA_URL,
    serve,
    vision_config_from_env,
)


class DemoVisionEnvTest(unittest.TestCase):
    def test_meta_vision_model_selects_local_with_default_url(self) -> None:
        config = vision_config_from_env(
            {"CONTEXTFLOW_OLLAMA_MODEL": "llama3.2-vision:11b"}
        )
        self.assertEqual(config["CONTEXTFLOW_VISION_PROVIDER"], "local")
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_MODEL"], "llama3.2-vision:11b")
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_URL"], DEFAULT_OLLAMA_URL)
        self.assertEqual(DEFAULT_OLLAMA_URL, "http://127.0.0.1:11434")

    def test_custom_url_respected(self) -> None:
        config = vision_config_from_env(
            {
                "CONTEXTFLOW_OLLAMA_URL": "http://127.0.0.1:11500",
                "CONTEXTFLOW_OLLAMA_MODEL": "llama3.2-vision:11b",
            }
        )
        self.assertEqual(config["CONTEXTFLOW_VISION_PROVIDER"], "local")
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_URL"], "http://127.0.0.1:11500")

    def test_missing_model_keeps_mock(self) -> None:
        config = vision_config_from_env({})
        self.assertEqual(config["CONTEXTFLOW_VISION_PROVIDER"], "mock")
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_MODEL"], "")

    def test_blank_model_keeps_mock(self) -> None:
        config = vision_config_from_env(
            {
                "CONTEXTFLOW_OLLAMA_URL": "http://127.0.0.1:11434",
                "CONTEXTFLOW_OLLAMA_MODEL": "   ",
            }
        )
        self.assertEqual(config["CONTEXTFLOW_VISION_PROVIDER"], "mock")

    def test_local_model_defaults_to_180s(self) -> None:
        config = vision_config_from_env(
            {"CONTEXTFLOW_OLLAMA_MODEL": "llama3.2-vision:11b"}
        )
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_TIMEOUT"], 180)
        self.assertEqual(DEFAULT_OLLAMA_TIMEOUT, 180)

    def test_timeout_env_override(self) -> None:
        config = vision_config_from_env(
            {
                "CONTEXTFLOW_OLLAMA_MODEL": "llama3.2-vision:11b",
                "CONTEXTFLOW_OLLAMA_TIMEOUT": "300",
            }
        )
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_TIMEOUT"], 300)

    def test_invalid_timeout_falls_back_to_default(self) -> None:
        for raw in ("schnell", "0", "-5"):
            with self.subTest(raw=raw):
                config = vision_config_from_env(
                    {
                        "CONTEXTFLOW_OLLAMA_MODEL": "llama3.2-vision:11b",
                        "CONTEXTFLOW_OLLAMA_TIMEOUT": raw,
                    }
                )
                self.assertEqual(config["CONTEXTFLOW_OLLAMA_TIMEOUT"], 180)

    def test_non_meta_local_model_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            vision_config_from_env({"CONTEXTFLOW_OLLAMA_MODEL": "qwen3-vl:2b"})
        self.assertIn("ausschließlich Meta-Modelle", str(raised.exception))

    def test_meta_cloud_is_explicit_and_never_copies_key_into_config(self) -> None:
        config = vision_config_from_env(
            {
                "CONTEXTFLOW_VISION_PROVIDER": "meta",
                "MODEL_API_KEY": "do-not-copy-this",
            }
        )
        self.assertEqual("meta", config["CONTEXTFLOW_VISION_PROVIDER"])
        self.assertTrue(config["CONTEXTFLOW_META_CONSENT"])
        self.assertEqual("https://api.meta.ai/v1/responses", config["CONTEXTFLOW_META_ENDPOINT"])
        self.assertEqual("muse-spark-1.3-contributor", config["CONTEXTFLOW_META_MODEL"])
        self.assertNotIn("MODEL_API_KEY", config)
        self.assertNotIn("do-not-copy-this", " ".join(map(str, config.values())))

    def test_meta_cloud_requires_api_key(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            vision_config_from_env({"CONTEXTFLOW_VISION_PROVIDER": "meta"})
        self.assertIn("MODEL_API_KEY is missing", str(raised.exception))


class DemoServeTest(unittest.TestCase):
    def test_busy_port_gives_actionable_message(self) -> None:
        # Real double bind (no listen needed): the preflight must
        # refuse before the app runs.
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        try:

            class NeverRuns:
                def run(self, **kwargs) -> None:
                    self.fail("app must not run on a busy port")

            with self.assertRaises(SystemExit) as raised:
                serve(NeverRuns(), port)
        finally:
            holder.close()
        message = str(raised.exception)
        self.assertIn(f"127.0.0.1:{port}", message)
        self.assertIn("--port", message)

    def test_forbidden_listen_gives_actionable_message(self) -> None:
        blocking = mock.Mock()
        blocking.listen.side_effect = OSError(10013, "Zugriff verweigert")
        with mock.patch("tools.contextflow_demo.socket") as sockmod:
            sockmod.socket.return_value = blocking
            sockmod.AF_INET = socket.AF_INET
            sockmod.SOCK_STREAM = socket.SOCK_STREAM
            with self.assertRaises(SystemExit) as raised:
                serve(mock.Mock(), 5100)
        message = str(raised.exception)
        self.assertIn("127.0.0.1:5100", message)
        self.assertIn("--port", message)
        blocking.close.assert_called_once_with()

    def test_clean_run_passes_through(self) -> None:
        seen: dict = {}

        class CleanApp:
            def run(self, **kwargs) -> None:
                seen.update(kwargs)

        with mock.patch("tools.contextflow_demo.socket") as sockmod:
            sockmod.socket.return_value = mock.Mock()
            sockmod.AF_INET = socket.AF_INET
            sockmod.SOCK_STREAM = socket.SOCK_STREAM
            serve(CleanApp(), 5100)
        self.assertEqual("127.0.0.1", seen["host"])
        self.assertEqual(5100, seen["port"])
        self.assertFalse(seen["debug"])
        self.assertFalse(seen["use_reloader"])

    def test_keyboard_interrupt_still_stops(self) -> None:
        class StoppingApp:
            def run(self, **kwargs) -> None:
                raise KeyboardInterrupt

        with mock.patch("tools.contextflow_demo.socket") as sockmod:
            sockmod.socket.return_value = mock.Mock()
            sockmod.AF_INET = socket.AF_INET
            sockmod.SOCK_STREAM = socket.SOCK_STREAM
            with self.assertRaises(KeyboardInterrupt):
                serve(StoppingApp(), 5100)


if __name__ == "__main__":
    unittest.main()
