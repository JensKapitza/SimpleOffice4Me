"""Authenticated entry point and game-switch regression coverage."""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # unittest discover -s tests
    from http_api_fixture import PublicHttpTestCase

from app import app


class KidsMiniGamePageTests(PublicHttpTestCase):
    def test_dashboard_links_to_play_break_with_a_visible_back_symbol(self):
        home = self.client.get("/kids/")
        self.assertEqual(200, home.status_code)
        self.assertIn('href="/kids/lernen/spielpause"', home.text)
        play_break = self.client.get("/kids/lernen/spielpause")
        self.assertEqual(200, play_break.status_code)
        for selector in ("data-mini-back", "data-result-back"):
            self.assertIn(f'{selector}><span aria-hidden="true">⬅️</span>', play_break.text)
        self.assertLess(play_break.text.index("data-mini-back"), play_break.text.index("data-mini-stage"))
        self.assertIn("js/kids_mini_games.js", play_break.text)
        self.assertEqual(302, app.test_client().get("/kids/lernen/spielpause").status_code)

    def test_memory_progress_hook_and_versioned_assets_are_local(self):
        page = self.client.get("/kids/lernen/spielpause")
        self.assertEqual(200, page.status_code)
        self.assertIn('data-mini-memory-progress aria-hidden="true" hidden', page.text)
        self.assertIn('data-mini-feedback role="status" aria-live="polite"', page.text)
        urls = re.findall(r'(?:src|href)="([^"]+/kids_mini_games\.(?:js|css)\?v=[^"]+)"', page.text)
        self.assertEqual(2, len(urls))
        for url in urls:
            with self.client.get(url) as asset:
                self.assertEqual(200, asset.status_code)
                self.assertIn(b"kids-mini-memory-slot", asset.data)


class KidsMiniGameFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend tests")
    def test_play_break_can_be_left_and_reopened_mid_turn(self):
        script = Path(__file__).with_name("kids_mini_games.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
