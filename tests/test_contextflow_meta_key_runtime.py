"""Runtime Meta key override: config wins over environ, never persisted.

Covers the "nach dem Start hinterlegen" path: an admin sets
``CONTEXTFLOW_META_API_KEY`` in process memory, and
``vision_settings_from_mapping`` reads it wherever the app fetches the
key, with the parent ``.env`` (``MODEL_API_KEY``) as fallback.
"""

from __future__ import annotations

import unittest

from app.contextflow.vision import vision_settings_from_mapping


class VisionRuntimeKeyTest(unittest.TestCase):
    def test_env_fallback(self) -> None:
        settings = vision_settings_from_mapping({}, {"MODEL_API_KEY": "env-key"})
        self.assertEqual(settings["meta_api_key"], "env-key")

    def test_runtime_config_wins_over_env(self) -> None:
        settings = vision_settings_from_mapping(
            {"CONTEXTFLOW_META_API_KEY": "runtime-key"},
            {"MODEL_API_KEY": "env-key"},
        )
        self.assertEqual(settings["meta_api_key"], "runtime-key")

    def test_empty_means_absent(self) -> None:
        settings = vision_settings_from_mapping({}, {})
        self.assertEqual(settings["meta_api_key"], "")
        settings = vision_settings_from_mapping(
            {"CONTEXTFLOW_META_API_KEY": "   "}, {"MODEL_API_KEY": ""}
        )
        self.assertEqual(settings["meta_api_key"], "")


if __name__ == "__main__":
    unittest.main()
