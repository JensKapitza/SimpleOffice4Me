"""Pinned English voice download stays opt-in, bounded and SHA-256 checked."""
from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import english_voice_assets as assets


PAYLOADS = {"fixture.onnx": b"English model fixture", "fixture.onnx.json": b'{"sample_rate":22050}'}
MANIFEST = tuple(assets.Asset(name, len(data), hashlib.sha256(data).hexdigest())
                 for name, data in PAYLOADS.items())


class Response(io.BytesIO):
    status = 200

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)


class Transport:
    def __init__(self, corrupt=False):
        self.urls = []
        self.corrupt = corrupt

    def open(self, request, *, timeout):
        self.urls.append(request.full_url)
        name = request.full_url.rsplit("/", 1)[-1]
        payload = PAYLOADS[name]
        if self.corrupt and name.endswith(".json"):
            payload += b"x"
        return Response(payload)


class EnglishVoiceAssetTests(unittest.TestCase):
    def fixture(self, root, transport):
        stack = patch.object(assets, "_ASSETS", MANIFEST), patch.object(assets, "MODEL_NAME", "fixture.onnx"), patch.object(
            assets, "build_opener", return_value=transport,
        )
        for item in stack:
            item.start()
            self.addCleanup(item.stop)
        return Path(root) / ".simpleoffice-meta" / "voices" / f"en-us-libritts-high-{assets.REVISION}"

    def test_installs_fixed_pair_verifies_hashes_and_reports_bounded_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            transport = Transport()
            directory = self.fixture(temp, transport)
            progress = []
            self.assertEqual("ready", assets.install(temp, progress=lambda *row: progress.append(row)))
            self.assertEqual(str(directory / "fixture.onnx"), assets.installed_model(temp))
            self.assertEqual([assets._BASE + item.name for item in MANIFEST], transport.urls)
            self.assertTrue(progress)
            self.assertEqual(sum(item.size for item in MANIFEST), progress[-1][1])
            self.assertEqual(sum(item.size for item in MANIFEST), progress[-1][2])

    def test_corruption_and_cancellation_do_not_publish_a_model(self):
        with tempfile.TemporaryDirectory() as temp:
            transport = Transport(corrupt=True)
            directory = self.fixture(temp, transport)
            self.assertEqual("failed", assets.install(temp))
            self.assertEqual("", assets.installed_model(temp))
            self.assertFalse((directory / "fixture.onnx").exists())

        with tempfile.TemporaryDirectory() as temp:
            transport = Transport()
            directory = self.fixture(temp, transport)
            cancelled = threading.Event()
            cancelled.set()
            self.assertEqual("cancelled", assets.install(temp, cancel_event=cancelled))
            self.assertEqual([], transport.urls)
            self.assertEqual("", assets.installed_model(temp))
            self.assertFalse(directory.joinpath("fixture.onnx").exists())

    def test_redirects_only_allow_https_model_hosts(self):
        handler = assets._Redirects()
        for target in ("http://huggingface.co/model", "https://example.org/model",
                       "https://huggingface.co:8443/model", "https://user@huggingface.co/model"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(assets.Request(assets._BASE), None, 302, "", {}, target)


if __name__ == "__main__":
    unittest.main()
