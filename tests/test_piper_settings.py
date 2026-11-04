"""Piper voice model comes from the media settings store, never from the environment."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app.audio_output_engine import _stored_piper_model, render_tts
from app.media_settings import (
    normalize_piper_model,
    validate_piper_model,
)
from app.settings_store import SettingsStore


class PiperModelSettingsTests(unittest.TestCase):
    def test_normalize_and_validate_piper_model(self):
        self.assertEqual("", normalize_piper_model(None))
        self.assertEqual("", normalize_piper_model("x" * 1001))
        self.assertEqual("/models/voice.onnx", normalize_piper_model("  /models/voice.onnx "))
        self.assertEqual("/models/voice.onnx", validate_piper_model("/models/voice.onnx"))
        self.assertEqual("", validate_piper_model(""))
        with self.assertRaises(ValueError):
            validate_piper_model("x" * 1001)
        with self.assertRaises(ValueError):
            validate_piper_model(None)

    def test_store_defaults_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            store = SettingsStore(root)
            self.assertEqual("", store.settings()["media"]["piper_model"])
            store.save({"media": {**store.settings()["media"], "piper_model": "/models/voice.onnx"}}, "test")
            self.assertEqual("/models/voice.onnx", SettingsStore(root).settings()["media"]["piper_model"])
            self.assertEqual("/models/voice.onnx", _stored_piper_model(root))

    def test_render_tts_ignores_environment_and_reports_settings(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"SIMPLEOFFICE_PIPER_MODEL": "/env/model.onnx"}):
                with self.assertRaisesRegex(RuntimeError, "Medien-Einstellungen"):
                    render_tts("Hallo", "stimme", root, document_root=root)

    def test_render_tts_uses_explicit_model_without_environment(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {}, clear=True):
                with patch("app.audio_output_engine.shutil.which", return_value=None):
                    with self.assertRaisesRegex(RuntimeError, "nicht installiert"):
                        render_tts("Hallo", "stimme", root, model="/models/voice.onnx")


if __name__ == "__main__":
    unittest.main()
