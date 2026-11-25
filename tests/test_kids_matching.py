"""Unit and integration tests for the Kids dashboard and matching game."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase

from app import app
from app.kids_matching import MATCHING_CATEGORIES, MATCHING_LEVELS, matching_config

ROOT = Path(__file__).resolve().parent.parent


class MatchingConfigTests(unittest.TestCase):
    def test_categories_cover_colors_shapes_animals_sounds(self):
        self.assertEqual({"colors", "shapes", "animals", "sounds"}, set(MATCHING_CATEGORIES))

    def test_every_item_has_symbol_and_bilingual_word(self):
        for category, items in MATCHING_CATEGORIES.items():
            self.assertGreaterEqual(len(items), 6, category)
            for item in items:
                with self.subTest(category=category, item=item):
                    self.assertTrue(item["emoji"].strip())
                    self.assertTrue(item["de"].strip())
                    self.assertTrue(item["en"].strip())
                    if category == "sounds":
                        self.assertTrue(item["sound_de"].strip())
                        self.assertTrue(item["sound_en"].strip())

    def test_items_unique_within_category(self):
        for category, items in MATCHING_CATEGORIES.items():
            words = [(item["emoji"], item["de"]) for item in items]
            self.assertEqual(len(words), len(set(words)), category)

    def test_levels_define_choices_and_short_rounds(self):
        self.assertEqual({"easy", "medium", "hard"}, set(MATCHING_LEVELS))
        self.assertEqual(
            [(2, 6), (3, 8), (4, 10)],
            [(MATCHING_LEVELS[key]["choices"], MATCHING_LEVELS[key]["rounds"]) for key in ("easy", "medium", "hard")],
        )
        for key, settings in MATCHING_LEVELS.items():
            with self.subTest(level=key):
                self.assertLessEqual(settings["choices"], min(len(items) for items in MATCHING_CATEGORIES.values()))
                self.assertLessEqual(settings["rounds"], 12)

    def test_config_is_json_safe(self):
        config = matching_config()
        self.assertEqual(config, json.loads(json.dumps(config, ensure_ascii=False)))


class KidsMatchingHttpTests(PublicHttpTestCase):
    def test_dashboard_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/kids", follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/auth/login", response.headers.get("Location", ""))

    def test_game_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/kids/zuordnen", follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))

    def test_dashboard_lists_every_kids_offer(self):
        response = self.client.get("/kids")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        for href in ("/kids/zuordnen", "/kids/lernen/", "/kids/coloring/"):
            self.assertIn(f'href="{href}"', html)
        self.assertIn("data-kids-dashboard", html)
        self.assertGreaterEqual(html.count("kids-symbol-card"), 6)

    def test_game_page_embeds_config_levels_and_repeat(self):
        response = self.client.get("/kids/zuordnen")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("data-kids-matching", html)
        self.assertIn('id="kids-matching-config"', html)
        for category in ("colors", "shapes", "animals", "sounds", "mixed"):
            self.assertIn(f'data-category="{category}"', html)
        for level in ("easy", "medium", "hard"):
            self.assertIn(f'data-level="{level}"', html)
        self.assertIn("data-repeat", html)
        self.assertIn("🔊", html)
        self.assertIn("js/kids_shared.js", html)
        self.assertIn("js/kids_matching.js", html)
        self.assertIn("css/kids_shared.css", html)
        self.assertIn("css/kids_matching.css", html)

    def test_sound_picture_clue_has_a_visual_control_after_login(self):
        for lang, label in (("de", "Bild-Tipp"), ("en", "Picture clue")):
            with self.subTest(lang=lang):
                response = self.client.post(
                    "/auth/language", data={"language": lang}, headers=self._csrf_headers(self.client, "/kids"),
                )
                self.assertEqual(302, response.status_code)
                html = self.client.get("/kids/zuordnen").get_data(as_text=True)
                self.assertIn(f'data-picture-hint aria-pressed="false" hidden><span aria-hidden="true">👁️</span> {label}', html)

    def test_kids_responses_stay_private(self):
        for path in ("/kids", "/kids/zuordnen"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual("private, no-store", response.headers.get("Cache-Control"))
                self.assertEqual("no-referrer", response.headers.get("Referrer-Policy"))
                self.assertEqual("noindex, nofollow", response.headers.get("X-Robots-Tag"))

    def test_rendered_pages_have_no_external_requests(self):
        for path in ("/kids", "/kids/zuordnen"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                # The random CSRF token may itself contain "cdn"/"http" – it is
                # not a request. Strip it so the test only sees real references.
                html = re.sub(r'<meta name="csrf-token"[^>]*>', "", html)
                self.assertNotIn("http://", html)
                self.assertNotIn("https://", html)
                self.assertNotIn("cdn", html.lower())

    def test_touch_targets_stay_above_44px(self):
        for name in ("kids_shared.css", "kids_matching.css"):
            css = (ROOT / "static" / "css" / name).read_text(encoding="utf-8")
            heights = [int(value) for value in re.findall(r"min-height:\s*(\d+)px", css)]
            self.assertTrue(heights, name)
            for height in heights:
                self.assertGreaterEqual(height, 44, f"{name}: {height}px")
        matching_css = (ROOT / "static" / "css" / "kids_matching.css").read_text(encoding="utf-8")
        answer = re.search(r"\.kids-matching-answer\s*\{[^}]*min-height:\s*(\d+)px", matching_css)
        self.assertIsNotNone(answer)
        self.assertGreaterEqual(int(answer.group(1)), 100)

    def test_game_usable_without_reading(self):
        html = self.client.get("/kids/zuordnen").get_data(as_text=True)
        self.assertIn("aria-label=", html)
        self.assertIn("role=", html)
        js = (ROOT / "static" / "js" / "kids_matching.js").read_text(encoding="utf-8")
        self.assertIn("aria-label", js)
        # Bewusste Spezifikationsänderung: Vorlesen nutzt die lokale
        # Piper-Serverstimme statt per-Gerät variierender Browser-Stimmen.
        self.assertIn("KidsPiperVoice", (ROOT / "static" / "js" / "kids_shared.js").read_text(encoding="utf-8"))
        self.assertIn("data-repeat", js)
        for hook in ("data-stage", "data-next", "data-category", "data-level", "data-result-title"):
            self.assertIn(hook, js)
            self.assertIn(hook, html)

    def test_game_logic_is_random_offline_and_self_contained(self):
        js = (ROOT / "static" / "js" / "kids_matching.js").read_text(encoding="utf-8")
        self.assertIn("Math.random", js)
        for forbidden in ("fetch(", "XMLHttpRequest", "http://", "https://", "cdn"):
            self.assertNotIn(forbidden, js)
        shared = (ROOT / "static" / "js" / "kids_shared.js").read_text(encoding="utf-8")
        self.assertIn("KidsShared", shared)
        for forbidden in ("fetch(", "XMLHttpRequest", "http://", "https://"):
            self.assertNotIn(forbidden, shared)

    def test_spoken_feedback_uses_server_piper_voice(self):
        # Bewusste Spezifikationsänderung: eine feste lokale Piper-Stimme
        # ersetzt alle Browser-Stimmen (Offline-first, Kids-Datenschutz).
        shared = (ROOT / "static" / "js" / "kids_shared.js").read_text(encoding="utf-8")
        self.assertIn("KidsPiperVoice", shared)
        self.assertNotIn("speechSynthesis", shared)
        self.assertNotIn("SpeechSynthesisUtterance", shared)


class KidsMatchingPictureHintTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend tests")
    def test_visual_clue_is_optional_and_does_not_leak_between_tasks(self):
        script = Path(__file__).with_name("kids_matching_picture_hint.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
