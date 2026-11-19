"""Pre-recorded kids game audio: inventory coverage, valid clips, page wiring."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase

from tools.generate_kids_game_audio import clip_path, inventory, is_valid_mp3


ROOT = Path(__file__).resolve().parent.parent


class KidsGameAudioInventoryTests(unittest.TestCase):
    def test_every_inventory_clip_exists_and_is_valid_mp3(self):
        bad = [
            str(clip_path(game, lang, key))
            for game, langs in inventory().items()
            for lang, clips in langs.items()
            for key in clips
            if not is_valid_mp3(clip_path(game, lang, key))
        ]
        self.assertEqual([], bad)

    def test_config_audio_keys_are_covered(self):
        from app.kids_matching import MATCHING_CATEGORIES
        from app.kids_numbers import NUMBERS_ITEMS

        clips = inventory()
        for category, items in MATCHING_CATEGORIES.items():
            for item in items:
                for field in ("prompt_audio", "word_audio"):
                    with self.subTest(item=item):
                        self.assertIn(item[field], clips["matching"]["de"])
                        self.assertIn(item[field], clips["matching"]["en"])
        for item in NUMBERS_ITEMS:
            for field in ("audio", "audio_plural"):
                with self.subTest(item=item):
                    self.assertIn(item[field], clips["numbers"]["de"])
                    self.assertIn(item[field], clips["numbers"]["en"])

    def test_js_fixed_clip_keys_are_covered(self):
        clips = inventory()
        fixed = {
            "matching": "static/js/kids_matching.js",
            "numbers": "static/js/kids_numbers.js",
            "syllables": "static/js/kids_syllables.js",
        }
        for game, relative in fixed.items():
            script = (ROOT / relative).read_text(encoding="utf-8")
            keys = set(re.findall(r"key: '([a-z0-9_]+)'", script))
            block = re.search(r"const CLIPS = \{(.*?)\};", script, re.DOTALL)
            if block:
                keys |= set(re.findall(r"'([a-z0-9_]+)'", block.group(1)))
            self.assertTrue(keys, game)
            for key in keys:
                with self.subTest(game=game, key=key):
                    self.assertIn(key, clips[game]["de"])
                    self.assertIn(key, clips[game]["en"])

    def test_syllables_dynamic_keys_match_the_dump(self):
        from tools.generate_kids_game_audio import syllables_dump

        dump = syllables_dump()
        clips = inventory()["syllables"]
        for lang in ("de", "en"):
            self.assertTrue(dump["words"][lang])
            self.assertTrue(dump["rhymes"][lang])
        self.assertIn("listen_well", clips["de"])

    def test_dynamic_key_families_are_complete(self):
        clips = inventory()
        for lang in ("de", "en"):
            numbers = clips["numbers"][lang]
            for number in range(1, 11):
                self.assertIn(f"num_{number}", numbers)
            for index in range(1, 5):
                self.assertIn(f"praise_{index}", numbers)
            syllables = clips["syllables"][lang]
            for key in ("has_1", "has_2", "has_3", "has_4", "try_1_of_5",
                        "try_2_of_5", "try_3_of_5", "try_4_of_5", "clap_more_1",
                        "clap_more_2", "clap_more_3"):
                self.assertIn(key, syllables)


class KidsGameAudioPageTests(PublicHttpTestCase):
    def test_game_pages_expose_audio_base_and_shared_helper(self):
        for path in ("/kids/zuordnen", "/kids/numbers/", "/kids/lernen/silben"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertIn("data-audio-base=", html)
                self.assertIn("data-audio-version=", html)
                self.assertIn("js/kids_shared.js", html)

    def test_clip_is_served_as_audio(self):
        response = self.client.get("/static/audio/kids/games/matching/de/find_rot.mp3")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.content_type.startswith("audio/"))
        self.assertGreater(len(response.data), 1000)
        response.close()


if __name__ == "__main__":
    unittest.main()
