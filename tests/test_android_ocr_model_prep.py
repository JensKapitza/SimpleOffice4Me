"""Build-only OCR model pins never install incomplete or mismatching data."""
from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import prepare_android_ocr_models as models


class AndroidOcrModelPrepTests(unittest.TestCase):
    def test_verified_existing_file_does_not_download_offline(self):
        fixture = b"synthetic model bytes"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "deu.traineddata").write_bytes(fixture)
            spec = {"deu.traineddata": (len(fixture), hashlib.sha256(fixture).hexdigest())}
            with patch.object(models, "MODELS", spec), patch.object(models.urllib.request, "build_opener") as builder:
                models.stage_models(path)
                builder.return_value.open.assert_not_called()

    def test_bad_download_never_overwrites_then_verified_content_is_promoted(self):
        fixture = b"synthetic model bytes"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            target = path / "deu.traineddata"
            target.write_bytes(b"known-incomplete")
            spec = {target.name: (len(fixture), hashlib.sha256(fixture).hexdigest())}
            with patch.object(models, "MODELS", spec), patch.object(models.urllib.request, "build_opener") as builder:
                download = builder.return_value.open
                for wrong in (fixture[:-1], fixture + b"!", b"A" * len(fixture)):
                    download.return_value = io.BytesIO(wrong)
                    with self.assertRaisesRegex(RuntimeError, "failed size/SHA-256"):
                        models.stage_models(path)
                    self.assertEqual(b"known-incomplete", target.read_bytes())
                download.return_value = io.BytesIO(fixture)
                models.stage_models(path)
                download.assert_called_with(f"{models.BASE}/{target.name}", timeout=30)
                self.assertEqual(fixture, target.read_bytes())
                self.assertEqual([target], list(path.iterdir()))
