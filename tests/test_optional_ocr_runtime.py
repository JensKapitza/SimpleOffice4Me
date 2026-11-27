"""Tests for the pinned local RapidOCR/ONNX runtime installer."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import optional_ocr_runtime as runtime
from app.ocr_runtime_worker import _result


class OptionalOcrRuntimeTests(unittest.TestCase):
    def test_support_is_limited_to_glibc_linux_x86_64_cpython_312(self):
        with patch.object(runtime.platform, "system", return_value="Linux"), \
                patch.object(runtime.platform, "machine", return_value="x86_64"), \
                patch.object(runtime.platform, "libc_ver", return_value=("glibc", "2.28")), \
                patch.object(runtime.sys, "version_info", (3, 12, 4)):
            self.assertTrue(runtime.install_supported())
            with patch.object(runtime.platform, "machine", return_value="aarch64"):
                self.assertFalse(runtime.install_supported())
            with patch.object(runtime.platform, "libc_ver", return_value=("musl", "1.2.4")):
                self.assertFalse(runtime.install_supported())
            with patch.object(runtime.sys, "version_info", (3, 13, 0)):
                self.assertFalse(runtime.install_supported())

    def test_current_flask_document_root_overrides_process_default(self):
        from flask import Flask

        app = Flask(__name__)
        app.config["DOCUMENT_ROOT"] = "/tmp/tenant-specific-documents"
        with patch.object(runtime, "_ROOT", Path("/tmp/process-default")), app.app_context():
            self.assertEqual(Path("/tmp/tenant-specific-documents"), runtime._document_root())

    def test_host_runtime_requires_all_bundled_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            models = Path(temporary) / "models"
            models.mkdir()
            for name in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"):
                (models / name).touch()
            rapidocr_spec = SimpleNamespace(submodule_search_locations=[temporary])
            with patch.object(runtime.importlib.util, "find_spec", side_effect=lambda name: {
                "onnxruntime": object(), "rapidocr": rapidocr_spec,
            }[name]):
                self.assertFalse(runtime.host_available())
                (models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx").touch()
                self.assertTrue(runtime.host_available())

    def test_wheel_manifest_is_fixed_and_uses_the_trusted_host(self):
        wheels = runtime.manifest()
        self.assertEqual(20, len(wheels))
        self.assertEqual(sum(row.size for row in wheels), runtime.artifact()["download_bytes"])
        for wheel in wheels:
            self.assertEqual("files.pythonhosted.org", runtime.urlsplit(wheel.url).hostname)
            self.assertEqual("https", runtime.urlsplit(wheel.url).scheme)
            self.assertEqual(64, len(wheel.digest))
        self.assertEqual(len(wheels), len(runtime.artifact()["wheels"]))
        self.assertTrue(all(row["license"] for row in runtime.artifact()["wheels"]))

    def test_redirect_policy_rejects_untrusted_destination(self):
        from urllib.request import Request

        handler = runtime._TrustedRedirects()
        request = Request(runtime.manifest()[0].url)
        allowed = handler.redirect_request(request, None, 302, "Found", {},
                                           "https://files.pythonhosted.org/package.whl")
        self.assertEqual("files.pythonhosted.org", allowed.host)
        with self.assertRaisesRegex(ValueError, "Untrusted OCR wheel"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://example.invalid/wheel")

    def test_download_rejects_bad_size_or_digest_before_promotion(self):
        wheel = runtime.Wheel("fixture.whl", 4, hashlib.sha256(b"good").hexdigest(),
                              "https://files.pythonhosted.org/fixture.whl")

        class Reply(io.BytesIO):
            status = 200
            headers = {"Content-Length": "4"}

            def geturl(self):
                return "https://files.pythonhosted.org/fixture.whl"

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / wheel.filename
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                runtime._download(wheel, destination, 10**12,
                                  opener=lambda *_args, **_kwargs: Reply(b"evil"))
            self.assertEqual(b"evil", destination.read_bytes(), "unverified wheel remains only in temporary staging")

    def test_manifest_binds_isolated_python_and_rejects_tampering_or_incomplete_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            components = runtime._components(root)
            runtime_dir = components / runtime._RUNTIME
            executable = runtime_dir / "bin" / "python"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"verified python executable")
            executable.chmod(0o700)
            record = {"version": runtime.VERSION, "runtime": runtime._RUNTIME,
                      "python_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
            self.assertEqual(executable, runtime._verified_python(components, record))
            executable.write_bytes(b"tampered python executable")
            self.assertIsNone(runtime._verified_python(components, record))
            record["python_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
            marker = runtime_dir / runtime._INCOMPLETE_MARKER
            marker.write_text(runtime.VERSION, encoding="ascii")
            self.assertIsNone(runtime._verified_python(components, record))

    def test_install_promotes_only_after_verified_wheels_and_smoke(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "documents"

            def download(wheel, destination, *_args, **_kwargs):
                destination.write_bytes(wheel.filename.encode("ascii"))

            def create_venv(_builder, directory):
                executable = Path(directory) / "bin" / "python"
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b"fixture python")
                executable.chmod(0o700)

            with patch.object(runtime, "install_supported", return_value=True), \
                    patch.object(runtime, "_download", side_effect=download), \
                    patch.object(runtime, "_stage_ready", return_value=True), \
                    patch.object(runtime, "_run", return_value=""), \
                    patch.object(runtime.venv.EnvBuilder, "create", create_venv), \
                    patch.object(runtime.shutil, "disk_usage", return_value=SimpleNamespace(free=2**30)):
                self.assertEqual("ready", runtime.install(root))
            self.assertTrue(runtime.ready(root))
            self.assertEqual(runtime.VERSION, runtime.installed_version(root))
            self.assertEqual("ready", runtime.install(root), "a verified runtime must be idempotent")

    def test_worker_result_bounds_text_and_confidence(self):
        fake = SimpleNamespace(txts=["  Hallo\nWelt  "], scores=[1.4], boxes=[[[1, 2], [3, 4]]])
        result = _result(lambda _path: fake, "local-image.png")
        self.assertEqual("Hallo Welt", result["text"])
        self.assertEqual(1.0, result["confidence"])
        self.assertEqual([[1.0, 2.0], [3.0, 4.0]], result["blocks"][0]["box"])

    def test_worker_result_iterates_numpy_like_arrays_without_truth_checks(self):
        class ArrayLike:
            def __init__(self, values):
                self.items = values

            def __bool__(self):
                raise ValueError("array truth value is ambiguous")

            def __iter__(self):
                return iter(self.items)

        fake = SimpleNamespace(txts=ArrayLike(["OCR 123"]), scores=ArrayLike([0.99]),
                               boxes=ArrayLike([ArrayLike([[1, 2], [3, 4]])]))
        result = _result(lambda _path: fake, "local-image.png")
        self.assertEqual("OCR 123", result["text"])
        self.assertEqual(0.99, result["confidence"])
        self.assertEqual([[1.0, 2.0], [3.0, 4.0]], result["blocks"][0]["box"])


if __name__ == "__main__":
    unittest.main()
