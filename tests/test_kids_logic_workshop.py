"""Real Kids navigation and an isolated read-only learning surface."""
from bs4 import BeautifulSoup

from app import app

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase

WORKSHOP = "/kids/lernen/logikwerkstatt"


class KidsLogicWorkshopTests(PublicHttpTestCase):
    def test_signed_in_kids_links_to_all_three_local_activities(self):
        home = self.client.get("/kids/")
        self.assertEqual(home.status_code, 200)
        page = self.client.get(WORKSHOP)
        self.assertEqual(page.status_code, 200)
        soup = BeautifulSoup(page.text, "html.parser")
        self.assertEqual(len(soup.select("main[data-kids-logic]")), 1)
        legend = soup.select_one(".logic-set-legend")
        self.assertEqual("group", legend.get("role"))
        self.assertTrue(legend.get("aria-label"))
        for anchor in ("schalter", "mengen", "sicherheit"):
            self.assertIn(f'href="{WORKSHOP}#{anchor}"', home.text)
            self.assertIsNotNone(soup.find(id=anchor))
        self.assertIn("private, no-store", page.headers["Cache-Control"])
        self.assertIn("camera=(), microphone=()", page.headers["Permissions-Policy"])
        self.assertIn("connect-src 'self'", page.headers["Content-Security-Policy"])
        for path in ("/static/js/kids_logic_workshop.mjs", "/static/js/kids_logic_rules.mjs",
                     "/static/css/kids_logic_workshop.css"):
            asset = self.client.get(path)
            self.addCleanup(asset.close)
            self.assertEqual(asset.status_code, 200)
        self.assertEqual(app.test_client().get(WORKSHOP).status_code, 302)

    def test_picture_story_and_reading_course_have_distinct_kids_tiles(self):
        csrf = self._csrf_headers(self.client, "/kids/")
        response = self.client.post("/documents/settings/language", data={"language": "de"}, headers=csrf)
        self.assertEqual(response.status_code, 302)
        home = self.client.get("/kids/")
        self.assertEqual(home.status_code, 200)
        links = BeautifulSoup(home.text, "html.parser")
        story = links.find("a", href=f"{WORKSHOP}#sicherheit")
        reading_course = links.select_one('a.kids-symbol-card[href^="/lernen/elektrotechnik"]')
        self.assertIsNotNone(story)
        self.assertIsNotNone(reading_course)
        self.assertEqual(story.select_one("span[aria-hidden=true]").get_text(strip=True), "🧑")
        self.assertEqual(reading_course.select_one("span[aria-hidden=true]").get_text(strip=True), "📖")
        self.assertEqual(story.select_one("strong").get_text(strip=True), "Bildergeschichte: Sicher bei Strom")
        self.assertEqual(reading_course.select_one("strong").get_text(strip=True), "Lesekurs: Stecker und Kabel")
        course_path, _, query = reading_course["href"].partition("?")
        self.assertEqual(course_path.rstrip("/"), "/lernen/elektrotechnik")
        self.assertEqual(query, "audience=kids")
        self.assertEqual(self.client.get(reading_course["href"]).status_code, 200)

    def test_story_is_separate_with_original_accessible_svgs_and_inactive_hardware(self):
        page = BeautifulSoup(self.client.get(WORKSHOP).text, "html.parser")
        story = page.select_one("#sicherheit")
        scenes = story.select("figure[data-story-scene]")
        self.assertEqual([node["data-story-scene"] for node in scenes],
                         ["outlet", "dropped", "siblings", "normal-switch"])
        self.assertEqual([node.has_attr("hidden") for node in scenes], [False, True, True, True])
        for area in (page.select_one("#schalter"), page.select_one("#mengen"), story):
            self.assertTrue(area.select("button"))
            self.assertTrue(all(node.get("type") == "button" for node in area.select("button")))
            self.assertFalse(area.select("video, audio, iframe, img, form"))
        for scene in scenes:
            self.assertTrue(scene.select_one('svg[role="img"]')["aria-label"])
            self.assertIsNotNone(scene.select_one("figcaption"))
        self.assertEqual([len(scene.select(".story-plug")) for scene in scenes], [1, 0, 0, 0])
        first = scenes[0].select_one('svg[role="img"]')
        plug = first.select_one('g.story-plug[aria-hidden="true"]')
        self.assertIsNotNone(plug)
        self.assertIs(plug.parent, first)
        self.assertIsNone(plug.select_one(".story-person"))
        self.assertFalse(scenes[-1].select(".story-outlet circle"))
        self.assertEqual(len(scenes[2].select(".story-child")), 2)
        self.assertEqual(len(scenes[2].select(".story-adult")), 1)
        # Transforms keep both children on the safe side of the outlet,
        # preserve their original sizes and keep the calling gesture visible.
        children = scenes[2].select(".story-child")
        self.assertEqual([child["transform"] for child in children],
                         ["matrix(0.72 0 0 0.72 160 111)", "matrix(0.95 0 0 0.95 214 111)"])
        self.assertEqual(scenes[2].select_one(".story-adult")["transform"], "matrix(1 0 0 1 306 105)")
        self.assertIsNone(children[0].select_one(".story-sound"))
        self.assertIsNotNone(children[1].select_one(".story-sound"))
        self.assertEqual(scenes[-1].select_one(".story-child")["transform"], "matrix(1 0 0 1 174 117)")

    def test_english_workshop_and_old_kids_electrical_safety_route(self):
        csrf = self._csrf_headers(self.client, "/kids/")
        response = self.client.post("/documents/settings/language", data={"language": "en"}, headers=csrf)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(WORKSHOP)
        self.assertIn('data-language="en"', response.text)
        self.assertIn("Picture story: safe around electricity", response.text)
        self.assertIn("Leave the plug alone. Step back and get a grown-up.", response.text)
        self.assertIn("Series (AND)", response.text)
        self.assertIn("OR · union", response.text)
        lesson = BeautifulSoup(response.text, "html.parser").find("a", string="Meet electricity safely")
        self.assertIsNotNone(lesson)
        self.assertEqual(self.client.get(lesson["href"]).status_code, 200)
