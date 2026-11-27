"""Exercise admin consent, pinned delivery and the normal Kids speech route."""
from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase

from app import app, kids_piper_assets as assets
from app.settings_store import SettingsStore


ADMIN = "/admin/settings/documents-media"
SETUP = ADMIN + "/kids-voice"
SPEECH = "/kids/lernen/sprache"
MAZE = {"text": "Gehe nach links, dann nach oben.", "lang": "de-DE", "purpose": "instructions"}
PAYLOADS = {"fixture.onnx": b"MODEL", "fixture.onnx.json": b'{"config":1}'}
MANIFEST = tuple(assets.Asset(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in PAYLOADS.items())


class Response(io.BytesIO):
    status = 200

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)


class Transport:
    def __init__(self, *, corrupt: bool = False) -> None:
        self.urls = []
        self.corrupt = corrupt

    def open(self, request, *, timeout):
        self.urls.append(request.full_url)
        name = request.full_url.rsplit("/", 1)[-1]
        payload = PAYLOADS[name]
        return Response(b"x" * len(payload) if self.corrupt and name.endswith(".json") else payload)


class KidsPiperSetupTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        self.transport = Transport()
        for mocked in (
            patch.object(assets, "_ASSETS", MANIFEST),
            patch.object(assets, "_MODEL_NAME", "fixture.onnx"),
            patch.object(assets, "build_opener", return_value=self.transport),
            patch.object(assets.shutil, "which", return_value="/usr/bin/piper"),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.headers = self._csrf_headers(self.client, ADMIN)

    def _setup(self, action="enable"):
        return self.client.post(SETUP, data={"action": action}, headers=self.headers)

    def test_page_is_disclosed_but_only_admin_post_makes_network_request(self):
        self.assertIn("CC0 1.0", self.client.get(ADMIN).text)
        self.assertIn("GPL-3.0-or-later", self.client.get(ADMIN).text)
        self.assertEqual(405, self.client.get(SETUP).status_code)
        self.assertEqual(403, self.client.post(SETUP, data={"action": "enable"}).status_code)
        anonymous = app.test_client()
        anonymous_result = anonymous.post(
            SETUP, data={"action": "enable"}, headers=self._csrf_headers(anonymous, "/auth/login"),
        )
        self.assertEqual(302, anonymous_result.status_code)
        self.assertTrue(anonymous_result.location.endswith("/auth/login"))
        other = app.test_client()
        self._register(self.client, "other")
        self._login(other, "other")
        self.assertEqual(403, other.post(
            SETUP, data={"action": "enable"}, headers=self._csrf_headers(other, "/kids/lernen/"),
        ).status_code)
        self.assertEqual(503, self.client.get(SPEECH, query_string=MAZE).status_code)
        self.assertEqual([], self.transport.urls)

    def test_activation_validates_both_files_then_reads_maze_instructions_only(self):
        rendered = Path(self.root.parent) / "test.wav"
        with wave.open(str(rendered), "wb") as audio:
            audio.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x00\x00" * 200)
        self.assertIn("kids_voice=enabled", self._setup().location)
        self.assertEqual([assets._MODEL_BASE + name for name in PAYLOADS], self.transport.urls)
        with patch("app.audio_output_engine.render_tts", return_value=rendered) as render:
            response = self.client.get(SPEECH, query_string=MAZE)
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.data.startswith(b"RIFF"))
            self.assertIn("no-store", response.headers["Cache-Control"])
            response.close()
            render.assert_called_once()
            self.assertEqual(MAZE["text"], render.call_args.args[0])
            self.assertTrue(render.call_args.kwargs["model"].endswith("/fixture.onnx"))
            for payload in (
                {**MAZE, "text": "Apfel"},
                {**MAZE, "purpose": ""},
                {**MAZE, "lang": "en-US"},
            ):
                self.assertEqual(503, self.client.get(SPEECH, query_string=payload).status_code)
            render.assert_called_once()
        self.assertIn("kids_voice=disabled", self._setup("disable").location)
        self.assertEqual(503, self.client.get(SPEECH, query_string=MAZE).status_code)
        self.assertIn("kids_voice=enabled", self._setup().location)
        self.assertEqual(2, len(self.transport.urls))
        model = Path(assets.enabled_model(self.root))
        model.write_bytes(b"X" * len(PAYLOADS[model.name]))
        self.assertEqual(503, self.client.get(SPEECH, query_string=MAZE).status_code)
        self.assertEqual(2, len(self.transport.urls), "a learning request must never redownload")

    def test_managed_render_has_a_single_slot_and_prunes_only_its_own_wavs(self):
        self.assertIn("kids_voice=enabled", self._setup().location)
        cache = assets._voice_dir(self.root) / "generated"
        leftovers = cache / "tts-render-crash"
        leftovers.mkdir(parents=True)
        (leftovers / "partial.wav").write_bytes(b"partial")
        for index in range(35):
            (cache / f"tts-{index}.wav").write_bytes(b"stale")
        unrelated = cache / "keep.txt"
        unrelated.touch()
        with (
            assets.exclusive_file_lock(cache / "render.lock"),
            patch("app.audio_output_engine.render_tts", side_effect=AssertionError("slot held")),
        ):
            response = self.client.get(SPEECH, query_string=MAZE)
            self.assertEqual(503, response.status_code)
            self.assertEqual("piper_busy", response.json["code"])

        def fake_render(_text, _voice, target_dir, **_options):
            target = target_dir / "tts-current.wav"
            target.write_bytes(b"RIFF")
            return target

        with patch("app.audio_output_engine.render_tts", side_effect=fake_render):
            response = self.client.get(SPEECH, query_string=MAZE)
            self.assertEqual(200, response.status_code)
            self.assertEqual(b"RIFF", response.data)
            response.close()
        self.assertEqual(32, len(list(cache.glob("tts-*.wav"))))
        self.assertTrue((cache / "tts-current.wav").is_file())
        self.assertTrue(unrelated.exists())
        self.assertFalse(leftovers.exists())

    def test_bad_download_leaves_no_active_asset_and_admin_can_retry(self):
        self.transport.corrupt = True
        self.assertIn("kids_voice=failed", self._setup().location)
        self.assertEqual("", assets.enabled_model(self.root))
        self.assertEqual([], list(assets._voice_dir(self.root).glob("*.onnx*")))
        self.assertEqual([], list(assets._voice_dir(self.root).glob("piper-download-*")))
        leftover = assets._voice_dir(self.root) / "piper-download-crash"
        leftover.mkdir()
        (leftover / "partial").write_bytes(b"partial")
        self.transport.corrupt = False
        self.assertIn("kids_voice=enabled", self._setup().location)
        self.assertFalse(leftover.exists())

    def test_missing_engine_never_downloads_and_existing_manual_setting_has_priority(self):
        with patch.object(assets.shutil, "which", return_value=None):
            self.assertIn("kids_voice=unavailable", self._setup().location)
        self.assertEqual([], self.transport.urls)
        self.assertIn("kids_voice=enabled", self._setup().location)
        store = SettingsStore(self.root)
        store.save({"media": {**store.settings()["media"], "piper_model": "/admin/voice.onnx"}}, "test")
        with patch("app.audio_output_engine.render_tts", side_effect=RuntimeError) as render:
            self.assertEqual(503, self.client.get(SPEECH, query_string=MAZE).status_code)
        self.assertEqual("/admin/voice.onnx", render.call_args.kwargs["model"])

    def test_install_lock_never_queues_a_second_download(self):
        with assets.exclusive_file_lock(assets._voice_dir(self.root) / "install.lock"):
            self.assertIn("kids_voice=busy", self._setup().location)
        self.assertEqual([], self.transport.urls)


class KidsPiperDownloadUnitTests(unittest.TestCase):
    def test_unknown_redirect_is_rejected_before_request(self):
        handler = assets._VoiceRedirects()
        for destination in (
            "http://huggingface.co/file",
            "https://example.com/file",
            "https://huggingface.co:8443/file",
            "https://user@huggingface.co/file",
        ):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                handler.redirect_request(assets.Request(assets._MODEL_BASE), None, 302, "", {}, destination)

    def test_download_stops_at_one_byte_over_expected_or_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            asset = MANIFEST[0]
            output = Path(root) / asset.name
            with patch.object(assets, "build_opener") as opener:
                opener.return_value.open.return_value = Response(b"x" * 100)
                with self.assertRaisesRegex(ValueError, "größer"):
                    assets._download(asset, output, time.monotonic() + 2)
                self.assertEqual(0, output.stat().st_size)
                output.unlink()
                opener.return_value.open.return_value = Response(PAYLOADS[asset.name])
                with self.assertRaises(TimeoutError):
                    assets._download(asset, output, time.monotonic() - 1)
                self.assertEqual(0, output.stat().st_size)

    def test_download_reports_verified_transfer_progress_and_honors_cancellation(self):
        with tempfile.TemporaryDirectory() as root:
            asset = MANIFEST[0]
            output = Path(root) / asset.name
            progress = []
            with patch.object(assets, "build_opener") as opener:
                opener.return_value.open.return_value = Response(PAYLOADS[asset.name])
                assets._download(asset, output, time.monotonic() + 2,
                                 progress=lambda name, received, total: progress.append((name, received, total)))
            self.assertEqual([(asset.name, asset.size, asset.size)], progress)
            self.assertEqual(PAYLOADS[asset.name], output.read_bytes())
            output.unlink()
            cancelled = threading.Event()
            cancelled.set()
            with patch.object(assets, "build_opener") as opener:
                opener.return_value.open.return_value = Response(PAYLOADS[asset.name])
                with self.assertRaises(InterruptedError):
                    assets._download(asset, output, time.monotonic() + 2, cancel_event=cancelled)
                opener.return_value.open.assert_not_called()
            self.assertFalse(output.exists())
