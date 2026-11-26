"""Public authenticated microphone route contract; no device exercised here."""
from app import app
from tests.http_api_fixture import PublicHttpTestCase

GAME = "/kids/lernen/silben"


class SyllableMicrophoneRouteTests(PublicHttpTestCase):
    def test_both_optional_modes_live_behind_existing_kids_entry(self):
        home = self.client.get("/kids/")
        self.assertEqual(200, home.status_code)
        self.assertIn(f'href="{GAME}"', home.text)
        for lang, clap, voice in [
            ("de", "Mit Mikrofon klatschen", "Mit Pausen sprechen"),
            ("en", "Use mic for claps", "Speak with pauses"),
        ]:
            with self.subTest(language=lang):
                self.client.post("/documents/settings/language", data={"language": lang},
                                 headers=self._csrf_headers(self.client))
                page = self.client.get(GAME)
                self.assertEqual(200, page.status_code)
                self.assertIn("camera=(), microphone=(self)", page.headers["Permissions-Policy"])
                self.assertIn("private, no-store", page.headers["Cache-Control"])
                self.assertIn(clap, page.text)
                self.assertIn(voice, page.text)
                self.assertIn("data-syll-mic hidden", page.text)
                self.assertIn("data-syll-mic-count", page.text)
                self.assertIn("data-syll-mic-target", page.text)
                self.assertIn("kids_syllable_signal.js", page.text)
                self.assertIn("kids_syllable_mic.js", page.text)

    def test_anonymous_and_every_other_kids_route_get_no_new_microphone_grant(self):
        anon = app.test_client().get(GAME)
        self.assertEqual(302, anon.status_code)
        self.assertIn("camera=(), microphone=()", anon.headers["Permissions-Policy"])
        for url in ("/kids/", "/kids/lernen/spielpause", "/kids/lernen/"):
            self.assertIn("camera=(), microphone=()", self.client.get(url).headers["Permissions-Policy"])
        preschool = self.client.get(GAME + "?practice=preschool")
        self.assertIn('data-syll-level="clap"', preschool.text)
        self.assertNotIn('data-syll-level="rhyme"', preschool.text)
