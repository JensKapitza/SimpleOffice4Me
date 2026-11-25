"""Run counting replay regressions alongside the authenticated page contract."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


ROOT = Path(__file__).resolve().parent.parent


class KidsNumbersRepeatTests(PublicHttpTestCase):
    def test_repeat_button_and_existing_local_question_feedback_clips(self):
        page = self.client.get("/kids/numbers/")
        self.assertEqual(200, page.status_code)
        self.assertIn('type="button" data-repeat', page.text)
        self.assertIn("kids_numbers.js", page.text)
        for language in ("de", "en"):
            for clip in ("how_many", "try_again"):
                with self.subTest(language=language, clip=clip):
                    url = f"/static/audio/kids/games/numbers/{language}/{clip}.mp3"
                    with self.client.get(url) as response:
                        self.assertEqual(200, response.status_code)
                        self.assertTrue(response.content_type.startswith("audio/"))

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser logic")
    def test_replay_uses_question_after_retry_and_in_silent_mode(self):
        result = subprocess.run(
            [shutil.which("node"), "--test", "tests/kids_numbers_repeat.test.cjs"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
