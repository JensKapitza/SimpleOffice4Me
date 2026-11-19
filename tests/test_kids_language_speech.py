"""Regression tests for Kids language subjects: English works, questions follow the subject language."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase

from app import app


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "js" / "kids_learning.js"
AUDIO_SCRIPT = ROOT / "static" / "js" / "kids_language_audio.js"
MINI_SCRIPT = ROOT / "static" / "js" / "kids_mini_games.js"
TEMPLATE = ROOT / "templates" / "kids" / "learning.html"


class KidsLanguageSubjectTests(unittest.TestCase):
    def test_english_round_uses_shared_vocabulary(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("englishWordCards", script)
        self.assertGreaterEqual(
            script.count("vocabularyForLevel(languageVocabulary, level)"), 3,
        )

    def test_german_questions_stay_german(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("const prompt = card.german_question_de;", script)
        self.assertIn("prompts/german/de/full/", script)
        self.assertNotIn("german_question_en", script)

    def test_english_questions_stay_english(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("card.english_early_en", script)
        self.assertIn("card.english_question_en", script)
        self.assertIn("prompts/english/en/", script)
        self.assertNotIn("english_early_de", script)
        self.assertNotIn("english_question_de", script)

    def test_empty_vocabulary_shows_friendly_notice(self):
        self.assertIn("data-setup-notice", TEMPLATE.read_text(encoding="utf-8"))
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("questions.some(question => !question || !question.q)", script)
        self.assertIn("data-setup-notice", script)

    def test_legacy_voice_ranking_prefers_on_device_voices(self):
        for path in (AUDIO_SCRIPT, MINI_SCRIPT):
            with self.subTest(script=path.name):
                script = path.read_text(encoding="utf-8")
                local = re.search(r"voice\.localService \? (\d+)", script)
                cloud = re.search(r"natural\|neural[^?]*\? (\d+)", script)
                self.assertIsNotNone(local)
                self.assertIsNotNone(cloud)
                self.assertGreater(int(local.group(1)), int(cloud.group(1)))


class KidsLearningPageTests(PublicHttpTestCase):
    def test_learning_page_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/kids/lernen/", follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))

    def test_learning_page_offers_all_subjects(self):
        html = self.client.get("/kids/lernen/").get_data(as_text=True)
        for subject in ("math", "german", "english", "world", "mixed", "search"):
            self.assertIn(f'data-subject="{subject}"', html)
        self.assertIn("data-setup-notice", html)
        self.assertIn("kids_language_audio.js", html)


if __name__ == "__main__":
    unittest.main()
