"""The optional preschool flow reuses both authenticated game entry points."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase

from app import app

ROOT = Path(__file__).resolve().parent.parent


class EntryPoints(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.hrefs: set[str] = set()
        self.levels: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        if tag == "a" and "href" in attributes:
            self.hrefs.add(attributes["href"])
        for level in ("data-level", "data-syll-level"):
            if level in attributes:
                self.levels.append(attributes[level])


class PreschoolRoundTests(PublicHttpTestCase):
    def test_dashboard_to_count_to_clap_to_dashboard_routes(self):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        dashboard = self.client.get("/kids/")
        self.assertEqual(200, dashboard.status_code)
        self.assertIn("Kleine Vorschulrunde", dashboard.text)
        count = next(link for link in EntryPoints(dashboard.text).hrefs
                     if link.startswith("/kids/numbers") and link.endswith("?practice=preschool"))

        counting = self.client.get(count)
        self.assertEqual(200, counting.status_code)
        self.assertIn("1 von 2: Zählen", counting.text)
        self.assertIn('data-preschool-round="true"', counting.text)
        self.assertEqual(["easy"], EntryPoints(counting.text).levels)
        clap = next(link for link in EntryPoints(counting.text).hrefs
                    if link.startswith("/kids/lernen/silben") and link.endswith("?practice=preschool"))

        clapping = self.client.get(clap)
        self.assertEqual(200, clapping.status_code)
        self.assertIn("2 von 2: Klatschen", clapping.text)
        self.assertEqual(["clap"], EntryPoints(clapping.text).levels)
        self.assertIn("/kids/", EntryPoints(clapping.text).hrefs)
        self.assertIn("Vorschulrunde geschafft", clapping.text)
        for page in (dashboard, counting, clapping):
            self.assertIn("no-store", page.headers["Cache-Control"])
            self.assertNotIn('src="http', page.text)
        guest = app.test_client()
        self.assertEqual(302, guest.get(count).status_code)
        self.assertEqual(302, guest.get(clap).status_code)

    def test_standard_levels_do_not_inherit_preschool_state(self):
        self.assertEqual(200, self.client.get("/kids/numbers/?practice=preschool").status_code)
        self.assertEqual(200, self.client.get("/kids/lernen/silben?practice=preschool").status_code)
        for url, levels in (
            ("/kids/numbers/", ["easy", "medium", "hard"]),
            ("/kids/numbers/?practice=unrecognized", ["easy", "medium", "hard"]),
            ("/kids/lernen/silben", ["clap", "count", "rhyme"]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(levels, EntryPoints(response.text).levels)
                self.assertNotIn("data-preschool-round", response.text)
                self.assertFalse(any("?practice=preschool" in link for link in EntryPoints(response.text).hrefs))

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser logic")
    def test_counting_steps_are_short_only_in_the_explicit_preset(self):
        result = subprocess.run(
            [shutil.which("node"), "--test", "tests/kids_preschool_round.test.cjs"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
