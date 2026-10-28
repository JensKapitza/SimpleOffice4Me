"""Focused start-config tests for the demo starter vision backend.

MODEL set -> local provider with URL + model; MODEL missing/blank ->
labeled mock for the finding-draft demo. No servers, no network access,
no API keys. Run with the project venv (``.venv``).
"""

from __future__ import annotations

import unittest

from tools.contextflow_demo import (
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_OLLAMA_URL,
    vision_config_from_env,
)


class DemoVisionEnvTest(unittest.TestCase):
    def test_model_selects_local_with_default_url(self) -> None:
        config = vision_config_from_env({"CONTEXTFLOW_OLLAMA_MODEL": "qwen3-vl:2b"})
        self.assertEqual(config["CONTEXTFLOW_VISION_PROVIDER"], "local")
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_MODEL"], "qwen3-vl:2b")
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_URL"], DEFAULT_OLLAMA_URL)
        self.assertEqual(DEFAULT_OLLAMA_URL, "http://127.0.0.1:11434")

    def test_custom_url_respected(self) -> None:
        config = vision_config_from_env(
            {
                "CONTEXTFLOW_OLLAMA_URL": "http://127.0.0.1:11500",
                "CONTEXTFLOW_OLLAMA_MODEL": "qwen3-vl:2b",
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
        config = vision_config_from_env({"CONTEXTFLOW_OLLAMA_MODEL": "qwen3-vl:2b"})
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_TIMEOUT"], 180)
        self.assertEqual(DEFAULT_OLLAMA_TIMEOUT, 180)

    def test_timeout_env_override(self) -> None:
        config = vision_config_from_env(
            {
                "CONTEXTFLOW_OLLAMA_MODEL": "qwen3-vl:2b",
                "CONTEXTFLOW_OLLAMA_TIMEOUT": "300",
            }
        )
        self.assertEqual(config["CONTEXTFLOW_OLLAMA_TIMEOUT"], 300)

    def test_invalid_timeout_falls_back_to_default(self) -> None:
        for raw in ("schnell", "0", "-5"):
            with self.subTest(raw=raw):
                config = vision_config_from_env(
                    {
                        "CONTEXTFLOW_OLLAMA_MODEL": "qwen3-vl:2b",
                        "CONTEXTFLOW_OLLAMA_TIMEOUT": raw,
                    }
                )
                self.assertEqual(config["CONTEXTFLOW_OLLAMA_TIMEOUT"], 180)

    def test_no_keys_or_cloud_paths(self) -> None:
        config = vision_config_from_env({"CONTEXTFLOW_OLLAMA_MODEL": "qwen3-vl:2b"})
        joined = " ".join(f"{key}={value}" for key, value in sorted(config.items()))
        self.assertNotIn("API_KEY", joined)
        self.assertNotIn("meta", joined.lower())
        self.assertNotIn("cloud", joined.lower())


if __name__ == "__main__":
    unittest.main()
