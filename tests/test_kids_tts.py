"""Kids read-aloud through the local Piper TTS endpoint (no browser voices).

Blackbox tests with synthetic texts only: the endpoint renders short
learning/game prompts (including maze announcements) with the configured
Piper model and caches them on disk. No customer data, no live AI calls.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase

from app import app
from app.settings_store import SettingsStore


ROOT = Path(__file__).resolve().parent.parent
KIDS_SCRIPTS = [
    ROOT / "static" / "js" / "kids_learning.js",
    ROOT / "static" / "js" / "kids_mini_games.js",
    ROOT / "static" / "js" / "kids_language_audio.js",
    ROOT / "static" / "js" / "kids_shared.js",
    ROOT / "static" / "js" / "kids_piper_voice.js",
]


def _write_fake_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b"\x00\x00" * 2205)


class KidsTtsEndpointTests(PublicHttpTestCase):
    def _save_media(self, **values):
        store = SettingsStore(Path(app.config["DOCUMENT_ROOT"]))
        settings = store.settings()
        settings["media"].update(values)
        store.save(settings, "test-kids-tts")

    def _render_response(self, text="Hallo Welt", lang="de-DE", **media):
        self._save_media(**media)
        calls = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        def fake_render(render_text, voice, cache_dir, model=None, **kwargs):
            calls.append({"text": render_text, "voice": voice, "model": model})
            target = Path(tmp.name) / "tts-test.wav"
            _write_fake_wav(target)
            return target

        with patch("app.audio_output_engine.render_tts", side_effect=fake_render):
            response = self.client.get(
                "/kids/lernen/sprache", query_string={"text": text, "lang": lang},
            )
        return response, calls

    def test_read_aloud_returns_local_audio(self):
        response, calls = self._render_response(
            piper_model="/models/de-thorsten.onnx",
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.content_type.startswith("audio/"))
        self.assertGreater(len(response.data), 1000)
        self.assertIn("max-age=", response.headers.get("Cache-Control", ""))
        self.assertNotIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertEqual(1, len(calls))
        self.assertEqual("Hallo Welt", calls[0]["text"])
        self.assertEqual("/models/de-thorsten.onnx", calls[0]["model"])
        response.close()

    def test_whitespace_variants_render_the_same_text(self):
        response, calls = self._render_response(
            text="  Hallo   Welt ", piper_model="/models/de-thorsten.onnx",
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("Hallo Welt", calls[0]["text"])
        response.close()

    def test_english_uses_english_model_with_german_fallback(self):
        response, calls = self._render_response(
            text="Find the way", lang="en-US",
            piper_model="/models/de-thorsten.onnx",
            piper_model_en="/models/en-libritts.onnx",
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("/models/en-libritts.onnx", calls[0]["model"])
        response.close()
        response, calls = self._render_response(
            text="Find the way", lang="en-US",
            piper_model="/models/de-thorsten.onnx",
            piper_model_en="",
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("/models/de-thorsten.onnx", calls[0]["model"])
        response.close()

    def test_rejects_bad_input(self):
        self._save_media(piper_model="/models/de-thorsten.onnx")
        for params in (
            {"text": "", "lang": "de-DE"},
            {"text": "   ", "lang": "de-DE"},
            {"text": "x" * 501, "lang": "de-DE"},
            {"text": "Hallo", "lang": "fr-FR"},
            {"text": "Hallo\x00Welt", "lang": "de-DE"},
        ):
            with self.subTest(params=params):
                response = self.client.get("/kids/lernen/sprache", query_string=params)
                self.assertEqual(400, response.status_code)
                self.assertIn("error", response.get_json())

    def test_missing_voice_reports_503_without_rendering(self):
        with patch(
            "app.audio_output_engine.render_tts",
            side_effect=AssertionError("must not render without a model"),
        ):
            response = self.client.get(
                "/kids/lernen/sprache",
                query_string={"text": "Hallo", "lang": "de-DE"},
            )
        self.assertEqual(503, response.status_code)
        payload = response.get_json()
        self.assertEqual("piper_unavailable", payload.get("code"))
        self.assertIn("Piper", payload.get("error", ""))

    def test_failed_render_reports_503_without_internals(self):
        self._save_media(piper_model="/models/de-thorsten.onnx")
        with patch(
            "app.audio_output_engine.render_tts",
            side_effect=RuntimeError("Piper ist nicht installiert"),
        ):
            response = self.client.get(
                "/kids/lernen/sprache",
                query_string={"text": "Hallo", "lang": "de-DE"},
            )
        self.assertEqual(503, response.status_code)
        payload = response.get_json()
        self.assertEqual("piper_unavailable", payload.get("code"))
        self.assertNotIn("nicht installiert", payload.get("error", ""))

    def test_read_aloud_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get(
            "/kids/lernen/sprache",
            query_string={"text": "Hallo", "lang": "de-DE"},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

    def test_endpoint_reuses_the_existing_tts_engine(self):
        source = (ROOT / "app" / "kids_learning.py").read_text(encoding="utf-8")
        self.assertIn("render_tts", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("http://", source)
        self.assertNotIn("https://", source)


class KidsMazePiperTests(PublicHttpTestCase):
    def test_no_browser_speech_synthesis_left_in_kids_js(self):
        for script in KIDS_SCRIPTS:
            with self.subTest(script=script.name):
                source = script.read_text(encoding="utf-8")
                self.assertNotIn("speechSynthesis", source)
                self.assertNotIn("SpeechSynthesisUtterance", source)

    def test_maze_announcements_use_the_piper_voice(self):
        learning = (ROOT / "static" / "js" / "kids_learning.js").read_text(encoding="utf-8")
        self.assertIn("speakMazeRoute", learning)
        # The maze speaks through the Piper-backed language audio object.
        self.assertIn("window.KidsLanguageAudio", learning)
        language_audio = (ROOT / "static" / "js" / "kids_language_audio.js").read_text(encoding="utf-8")
        self.assertIn("KidsPiperVoice", language_audio)
        shared = (ROOT / "static" / "js" / "kids_piper_voice.js").read_text(encoding="utf-8")
        self.assertIn("/kids/lernen/sprache", shared)

    def test_templates_wire_the_piper_client(self):
        for name in ("learning.html", "mini_games.html", "matching.html",
                     "numbers.html", "syllables.html"):
            with self.subTest(template=name):
                html = (ROOT / "templates" / "kids" / name).read_text(encoding="utf-8")
                self.assertIn("kids_piper_voice.js", html)
                self.assertIn("data-tts-url", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend tests")
    def test_maze_audio_warning_recovers_only_after_current_playback(self):
        script = Path(__file__).with_name("kids_language_audio.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_learning_page_serves_piper_wiring(self):
        html = self.client.get("/kids/lernen/").get_data(as_text=True)
        self.assertIn("kids_piper_voice.js", html)
        self.assertIn("data-tts-url", html)
        self.assertNotIn("data-speech-voice", html)


if __name__ == "__main__":
    unittest.main()
