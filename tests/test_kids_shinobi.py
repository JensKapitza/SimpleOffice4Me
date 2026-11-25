"""Authenticated UI and privacy contracts for the standalone visual mission."""
from html.parser import HTMLParser
from unittest.mock import patch

from app import app
from tests.http_api_fixture import PublicHttpTestCase

GAME = "/kids/lernen/shinobi"


class MissionAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.media = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag in {"audio", "video", "iframe", "input"}:
            self.media.append(tag)
        value = fields.get("src") or fields.get("href")
        if value and "kids_shinobi" in value:
            self.urls.append(value)


class ShinobiRouteTests(PublicHttpTestCase):
    def test_anonymous_cannot_open_the_game(self):
        response = app.test_client().get(GAME)
        self.assertEqual(302, response.status_code)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_kids_home_to_local_mission_in_both_languages(self):
        for lang, title, intro in [
            ("de", "Tavis Shinobi-Mission", "gehörloser Shinobi"),
            ("en", "Tavi’s Shinobi mission", "deaf Shinobi"),
        ]:
            with self.subTest(lang=lang):
                selected = self.client.post("/documents/settings/language", data={"language": lang},
                                            headers=self._csrf_headers(self.client))
                self.assertEqual(302, selected.status_code)
                entry = self.client.get("/kids/")
                self.assertEqual(200, entry.status_code)
                self.assertIn(f'href="{GAME}"', entry.get_data(as_text=True))
                page = self.client.get(GAME)
                body = page.get_data(as_text=True)
                self.assertIn(title, body)
                self.assertIn(intro, body)
                self.assertNotIn("taubstumm", body.casefold())
                self.assertIn('data-kids-header', body)
                has_hand_game = "kids_learning.hand_game" in app.view_functions
                self.assertEqual(has_hand_game, "data-shinobi-hand-link" in body)
                if has_hand_game:
                    self.assertIn('data-shinobi-hand-link href="/kids/lernen/handzeichen"', body)
                    separate_game = self.client.get("/kids/lernen/handzeichen")
                    self.assertEqual(200, separate_game.status_code)
                    self.assertIn("camera=(self)", separate_game.headers["Permissions-Policy"])
                self.assertIn("camera=(), microphone=()", page.headers["Permissions-Policy"])
                self.assertIn('data-shinobi-start disabled', body)
                parser = MissionAssets()
                parser.feed(body)
                self.assertFalse(parser.media)
                for url in set(parser.urls):
                    self.assertTrue(url.startswith("/static/"))
                    response = self.client.get(url.split("#")[0])
                    try:
                        self.assertEqual(200, response.status_code)
                    finally:
                        response.close()
                self.assertTrue(any(url.endswith("#tavi") for url in parser.urls))
                self.assertTrue(any("kids_shinobi.js" in url for url in parser.urls))

    def test_no_separate_link_when_hand_game_is_not_registered(self):
        with patch.object(app, "view_functions", {
            name: handler for name, handler in app.view_functions.items() if name != "kids_learning.hand_game"
        }):
            response = self.client.get(GAME)
            self.assertEqual(200, response.status_code)
            self.assertNotIn("data-shinobi-hand-link", response.get_data(as_text=True))
