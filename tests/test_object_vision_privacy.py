"""The optional RapidOCR provider is initialized with the documented 1DS opt-out."""
from __future__ import annotations

import builtins
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import object_vision


class ObjectVisionPrivacyTests(unittest.TestCase):
    def test_opt_out_precedes_rapidocr_import_and_construction(self):
        real_import = builtins.__import__
        for telemetry in (None, "0"):
            with self.subTest(initial=telemetry), tempfile.TemporaryDirectory() as root:
                env = {"OCR_LOCAL_TEST_MARKER": "unchanged"}
                if telemetry is not None:
                    env["ORT_DISABLE_TELEMETRY"] = telemetry
                expected = {**env, "ORT_DISABLE_TELEMETRY": "1"}
                model_dir = Path(root) / "models"
                model_dir.mkdir()
                for name in ("PP-OCRv6_det_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                             "PP-OCRv6_rec_small.onnx"):
                    (model_dir / name).touch()
                marker = Mock()
                phases = []

                def construct(**kwargs):
                    self.assertEqual(expected, dict(os.environ))
                    self.assertTrue(all(Path(path).is_file() for key, path in kwargs["params"].items()
                                        if key.endswith(".model_path")))
                    phases.append("construction")
                    return marker

                def fake_import(name, *args, **kwargs):
                    if name == "rapidocr":
                        self.assertEqual(expected, dict(os.environ))
                        phases.append("import")
                        return SimpleNamespace(RapidOCR=construct)
                    return real_import(name, *args, **kwargs)

                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(object_vision, "_RAPID_OCR_ENGINE", None),
                    patch.object(object_vision, "find_spec", return_value=SimpleNamespace(
                        submodule_search_locations=[root],
                    )),
                    patch("builtins.__import__", side_effect=fake_import),
                ):
                    self.assertIs(marker, object_vision._rapidocr_engine())
                    self.assertIs(marker, object_vision._rapidocr_engine())
                    self.assertEqual(expected, dict(os.environ))
                self.assertEqual(["import", "construction"], phases)

    def test_missing_optional_engine_uses_existing_fallback_without_setting_environment(self):
        fallback = {"engine": "tesseract", "status": "completed", "text": "SYNTHETIC RECEIPT"}
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(object_vision, "_RAPID_OCR_ENGINE", None),
                patch.object(object_vision, "find_spec", return_value=SimpleNamespace(
                    submodule_search_locations=[root],
                )),
                patch.object(object_vision, "_run_tesseract", return_value=fallback) as tesseract,
            ):
                path = Path(root) / "fixture.png"
                result = object_vision.analyze_ocr(path)
                self.assertNotIn("ORT_DISABLE_TELEMETRY", os.environ)
        tesseract.assert_called_once_with(path)
        self.assertEqual("tesseract", result["engine"])
        self.assertEqual("SYNTHETIC RECEIPT", result["text"])
