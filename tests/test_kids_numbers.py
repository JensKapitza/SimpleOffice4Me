"""Counting and quantities game: config unit tests plus page integration tests."""
import re
import sys
import unittest
from pathlib import Path

# Keep this module runnable both via `discover -s tests` and via
# `python -m unittest tests.test_kids_numbers` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from http_api_fixture import PublicHttpTestCase  # noqa: E402

from app import app  # noqa: E402
from app import kids_numbers  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "kids" / "numbers.html"
SCRIPT = ROOT / "static" / "js" / "kids_numbers.js"
STYLES = ROOT / "static" / "css" / "kids_numbers.css"


class KidsNumbersConfigTests(unittest.TestCase):
    def test_levels_cover_easy_medium_hard(self):
        self.assertEqual({"easy", "medium", "hard"}, set(kids_numbers.NUMBERS_LEVELS))
        for name, level in kids_numbers.NUMBERS_LEVELS.items():
            with self.subTest(level=name):
                self.assertEqual({"max", "choices", "tasks", "compare"}, set(level))
                self.assertGreaterEqual(level["max"], 2)
                self.assertLessEqual(level["max"], 10)
                self.assertGreaterEqual(level["choices"], 2)
                self.assertLessEqual(level["choices"], level["max"])
                # Short fixed rounds keep a full game within about 5-15 minutes.
                self.assertGreaterEqual(level["tasks"], 5)
                self.assertLessEqual(level["tasks"], 12)
                self.assertIsInstance(level["compare"], bool)

    def test_levels_increase_difficulty(self):
        easy = kids_numbers.NUMBERS_LEVELS["easy"]
        medium = kids_numbers.NUMBERS_LEVELS["medium"]
        hard = kids_numbers.NUMBERS_LEVELS["hard"]
        self.assertLess(easy["max"], medium["max"])
        self.assertLess(medium["max"], hard["max"])
        self.assertFalse(easy["compare"])
        self.assertTrue(medium["compare"])
        self.assertTrue(hard["compare"])

    def test_items_are_speakable_without_reading(self):
        self.assertGreaterEqual(len(kids_numbers.NUMBERS_ITEMS), 6)
        for item in kids_numbers.NUMBERS_ITEMS:
            with self.subTest(item=item.get("de")):
                self.assertEqual({"emoji", "de", "de_plural", "en", "en_plural"}, set(item))
                for key, value in item.items():
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip(), f"{key} must not be blank")


class KidsNumbersPageTests(PublicHttpTestCase):
    def test_numbers_page_renders_game_shell(self):
        response = self.client.get("/kids/numbers/")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("data-kids-numbers", html)
        self.assertIn('id="kids-numbers-config"', html)
        self.assertIn("kids_numbers.js", html)
        self.assertIn("kids_numbers.css", html)
        for level in ("easy", "medium", "hard"):
            self.assertIn(f'data-level="{level}"', html)
        # The default test account uses English; German is covered below.
        self.assertIn("Counting fun", html)
        # Spoken prompts keep the game usable without reading skills.
        self.assertIn("Hear again", html)
        self.assertIn("data-speech-status", html)

    def test_numbers_page_has_private_kids_headers(self):
        response = self.client.get("/kids/numbers/")
        self.assertEqual(200, response.status_code)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertEqual("no-referrer", response.headers.get("Referrer-Policy"))
        self.assertEqual("noindex, nofollow", response.headers.get("X-Robots-Tag"))

    def test_numbers_page_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/kids/numbers/")
        self.assertEqual(302, response.status_code)
        self.assertIn("/auth/login", response.headers.get("Location", ""))

    def test_numbers_page_links_kids_activities(self):
        response = self.client.get("/kids/numbers/")
        html = response.get_data(as_text=True)
        self.assertIn("/kids/lernen", html)
        self.assertIn("/kids/coloring", html)

    def test_numbers_page_has_no_external_requests(self):
        response = self.client.get("/kids/numbers/")
        html = response.get_data(as_text=True)
        self.assertNotIn('src="http', html)
        self.assertNotIn("src='http", html)
        self.assertNotIn('href="http', html)
        self.assertNotIn("href='http", html)


class KidsNumbersAssetsTests(unittest.TestCase):
    def test_script_speaks_and_needs_no_network(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("speechSynthesis", script)
        self.assertIn("kids-numbers-config", script)
        for forbidden in ("fetch(", "XMLHttpRequest", "http://", "https://"):
            self.assertNotIn(forbidden, script)

    def test_script_covers_all_task_kinds(self):
        script = SCRIPT.read_text(encoding="utf-8")
        for kind in ("'count'", "'show'", "'more'", "'less'"):
            self.assertIn(kind, script)

    def test_touch_targets_are_at_least_44px(self):
        css = STYLES.read_text(encoding="utf-8")
        heights = [int(value) for value in re.findall(r"min-height:\s*(\d+)px", css)]
        self.assertTrue(heights, "kids_numbers.css must define min-height touch targets")
        for height in heights:
            self.assertGreaterEqual(height, 44, "touch target below 44px")
        self.assertIn("touch-action", css)

    def test_feedback_and_result_regions_are_announced(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("data-feedback", template)
        self.assertIn("data-result", template)
        self.assertIn("aria-live", template)

    def test_template_carries_german_kid_texts(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        for german in ("Zählspaß", "Nochmal hören", "Wie weit kannst du zählen?",
                       "Zählen bis 3", "Zahlen bis 10", "Weiter", "Nochmal spielen"):
            self.assertIn(german, template)


if __name__ == "__main__":
    unittest.main()
