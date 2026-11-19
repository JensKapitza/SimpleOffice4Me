"""Kids mode: kids pages stay inside the kids area plus a grown-ups exit gate."""
from __future__ import annotations

import re
import unittest

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase

from app import app


KIDS_PAGES = (
    "/kids",
    "/kids/",
    "/kids/zuordnen",
    "/kids/numbers",
    "/kids/numbers/",
    "/kids/lernen/",
    "/kids/lernen/spielpause",
    "/kids/lernen/silben",
    "/kids/coloring/",
    "/lernen/elektrotechnik?audience=kids",
)


class KidsModeContainmentTests(PublicHttpTestCase):
    def test_kids_pages_hide_global_nav_toolbar_and_palette(self):
        for path in KIDS_PAGES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn("data-kids-header", html)
                self.assertNotIn('id="nav-kids"', html)
                self.assertNotIn("data-web-export-toolbar", html)
                self.assertNotIn("v3_command_palette.js", html)

    def test_kids_header_links_only_kids_offers_and_exit(self):
        html = self.client.get("/kids").get_data(as_text=True)
        header = html.split("data-kids-header")[1].split("</header>")[0]
        for href in ("/kids", "/kids/zuordnen", "/kids/numbers", "/kids/lernen/silben",
                     "/kids/lernen/spielpause", "/kids/lernen", "/kids/coloring",
                     "/kids/erwachsene"):
            self.assertIn(href, header)
        self.assertNotIn("/documents", header)
        self.assertNotIn("/auth/logout", header)

    def test_dashboard_links_all_new_games(self):
        html = self.client.get("/kids").get_data(as_text=True)
        self.assertIn('"/kids/numbers', html)
        self.assertIn('href="/kids/lernen/silben"', html)
        self.assertIn('href="/kids/zuordnen"', html)

    def test_adult_pages_keep_full_nav(self):
        response = self.client.get("/lernen/elektrotechnik")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('id="nav-kids"', html)
        self.assertNotIn("data-kids-header", html)

    def test_global_nav_links_kids_overview_directly_without_submenu(self):
        html = self.client.get("/documents/").get_data(as_text=True)
        self.assertIn('id="nav-kids"', html)
        self.assertIn('href="/kids/"', html)
        self.assertNotIn("Kinder-Übersicht", html)
        self.assertNotIn("Kids overview", html)

    def test_game_pages_have_a_single_unified_nav(self):
        for path in ("/kids/zuordnen", "/kids/numbers", "/kids/lernen/silben",
                     "/kids/lernen/spielpause", "/kids/lernen/", "/kids/coloring/"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertEqual(1, html.count("<nav"), path)
                self.assertIn("data-kids-header", html)


class KidsExitGateTests(PublicHttpTestCase):
    def test_exit_gate_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/kids/erwachsene", follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/auth/login", response.headers.get("Location", ""))

    def test_exit_gate_asks_arithmetic_and_leaves_on_correct_answer(self):
        page = self.client.get("/kids/erwachsene")
        self.assertEqual(200, page.status_code)
        html = page.get_data(as_text=True)
        match = re.search(r"(\d+) \+ (\d+) =\s*\?", html)
        self.assertIsNotNone(match)
        headers = self._csrf_headers(self.client, "/kids/erwachsene")
        # The helper GET above regenerated the question; answer the fresh one.
        fresh = self.client.get("/kids/erwachsene").get_data(as_text=True)
        match = re.search(r"(\d+) \+ (\d+) =\s*\?", fresh)
        self.assertIsNotNone(match)
        answer = int(match.group(1)) + int(match.group(2))
        response = self.client.post(
            "/kids/erwachsene", data={"answer": str(answer)},
            headers=headers, follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/documents", response.headers.get("Location", ""))

    def test_exit_gate_rejects_wrong_answer_without_leaving(self):
        self.client.get("/kids/erwachsene")
        headers = self._csrf_headers(self.client, "/kids/erwachsene")
        response = self.client.post(
            "/kids/erwachsene", data={"answer": "99999"}, headers=headers,
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("kids-exit-answer", html)
        self.assertIn("data-kids-header", html)

    def test_exit_gate_post_without_question_stays_put(self):
        headers = self._csrf_headers(self.client, "/kids/erwachsene")
        # Impossible answer: session answers range 12-38, so this deterministically
        # exercises the error path regardless of leaked session state.
        response = self.client.post(
            "/kids/erwachsene", data={"answer": "99999"}, headers=headers,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("kids-exit-answer", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
