"""Kids syllable game page contracts exercised through the HTTP interface."""
import re
from pathlib import Path

from http_api_fixture import PublicHttpTestCase

from app import app

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "templates" / "kids" / "syllables.html"
SCRIPT = REPO_ROOT / "static" / "js" / "kids_syllables.js"
STYLES = REPO_ROOT / "static" / "css" / "kids_syllables.css"


class KidsSyllablesPageTests(PublicHttpTestCase):
    def test_syllables_page_renders_for_logged_in_user(self):
        page = self.client.get("/kids/lernen/silben")
        self.assertEqual(200, page.status_code)
        html = page.get_data(as_text=True)
        for marker in (
            "data-kids-syllables",
            "kids_syllables.js",
            "kids_syllables.css",
            'data-syll-level="clap"',
            'data-syll-level="count"',
            'data-syll-level="rhyme"',
            "data-syll-play",
            "data-syll-result",
            "data-syll-repeat",
            "data-syll-next",
            "data-syll-back",
            "data-play-again",
        ):
            self.assertIn(marker, html)
        self.assertEqual("private, no-store", page.headers.get("Cache-Control"))
        self.assertEqual("no-referrer", page.headers.get("Referrer-Policy"))
        self.assertEqual("noindex, nofollow", page.headers.get("X-Robots-Tag"))

    def test_syllables_page_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/kids/lernen/silben")
        self.assertEqual(302, response.status_code)
        self.assertIn("/auth/login", response.headers.get("Location", ""))

    def test_syllables_copy_follows_language_pattern(self):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        german = self.client.get("/kids/lernen/silben").get_data(as_text=True)
        self.assertIn("Silben klatschen", german)
        self.assertIn("Nochmal hören", german)
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        english = self.client.get("/kids/lernen/silben").get_data(as_text=True)
        self.assertIn("Syllable clapping", english)
        self.assertIn("Hear again", english)

    def test_syllables_is_linked_from_kids_navigation(self):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        html = self.client.get("/kids/lernen/silben").get_data(as_text=True)
        self.assertIn("/kids/lernen/silben", html)
        pause = self.client.get("/kids/lernen/spielpause").get_data(as_text=True)
        self.assertIn("/kids/lernen/silben", pause)

    def test_syllables_usable_without_reading(self):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        html = self.client.get("/kids/lernen/silben").get_data(as_text=True)
        # Spoken guidance plus live regions; emoji carry meaning without text.
        self.assertIn("Nochmal hören", html)
        self.assertGreaterEqual(html.count('aria-live="polite"'), 3)
        self.assertIn("👏", html)
        self.assertIn("🔢", html)
        self.assertIn("🎵", html)
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("KidsSyllables", script)
        self.assertIn("KidsShared", script)
        self.assertIn("playClips", script)
        self.assertIn("aria-label", script)

    def test_syllables_touch_targets_are_large_enough(self):
        css = STYLES.read_text(encoding="utf-8")
        heights = [int(value) for value in re.findall(r"min-height:(\d+)px", css)]
        self.assertTrue(heights, "syllable CSS must pin touch target heights")
        self.assertTrue(all(value >= 44 for value in heights), heights)
        self.assertIn("min-height:68px", css.replace(" ", ""))

    def test_syllables_stays_offline_first(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")
        css = STYLES.read_text(encoding="utf-8")
        for blob, name in ((html, "template"), (script, "script"), (css, "styles")):
            self.assertNotIn("http://", blob, name)
            self.assertNotIn("https://", blob, name)
            self.assertNotIn("fetch(", blob, name)
            self.assertNotIn("XMLHttpRequest", blob, name)
        self.assertNotIn("@import", css)
        # Only local static assets may be referenced.
        for match in re.findall(r"url\(([^)]+)\)", css):
            self.assertFalse(match.strip().strip("'\"").startswith("http"), match)
        self.assertNotIn("cdn", html.lower() + script.lower())

    def test_spoken_feedback_uses_server_piper_voice(self):
        # Bewusste Spezifikationsänderung: eine feste lokale Piper-Stimme
        # ersetzt alle Browser-Stimmen (Offline-first, Kids-Datenschutz).
        shared = (REPO_ROOT / "static" / "js" / "kids_shared.js").read_text(encoding="utf-8")
        self.assertIn("KidsPiperVoice", shared)
        self.assertNotIn("speechSynthesis", shared)
        self.assertNotIn("SpeechSynthesisUtterance", shared)

    def test_game_uses_prerecorded_clip_keys(self):
        script = SCRIPT.read_text(encoding="utf-8")
        for key in ("listen_well", "tap_each_syllable", "praise_right", "praise_great",
                    "how_many_syllables", "what_rhymes", "look_solution", "done_great",
                    "nice_playing"):
            self.assertIn(f"'{key}'", script)
