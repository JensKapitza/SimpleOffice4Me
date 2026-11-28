"""Inspect the private birthday review flow using ordinary authenticated HTTP."""
from bs4 import BeautifulSoup

from app import app
from app.contact_store import ContactStore
from tests.http_api_fixture import PublicHttpTestCase


class ContactBirthdayReviewTests(PublicHttpTestCase):
    path = "/documents/contacts/birthdays"

    def _page(self, url):
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        return BeautifulSoup(response.data, "html.parser")

    def test_review_shows_both_sources_and_clears_after_explicit_correction(self):
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        mail = "same@example.test"
        store.upsert({"display_name": "Synthetic first", "email": mail, "birthday": "--05-01"},
                     "owner", "first", {"provider": "Fixture source one"})
        store.upsert({"display_name": "Synthetic second", "email": mail, "birthday": "2000-05-02"},
                     "owner", "second", {"provider": "Fixture source two"})
        store.upsert({"display_name": "Private third", "email": mail, "birthday": "--05-03"},
                     "other", "private", {"provider": "Private source"})
        store.upsert({"display_name": "No conflict", "birthday": "--05-04"}, "owner", "plain")

        page = self._page(self.path + "?view=review")
        self.assertEqual("Zu prüfen (2)", page.select_one('nav[aria-label="Geburtstage filtern"] [aria-current="page"]').get_text(strip=True))
        rows = page.select("main tbody tr")
        self.assertEqual(2, len(rows))
        first = rows[0].get_text(" ", strip=True)
        for value in ("--05-01", "Jahr unbekannt", "2000-05-02", "Fixture source one",
                      "Fixture source two", "Geburtstag prüfen"):
            self.assertIn(value, first)
        self.assertNotIn("Private source", page.get_text())
        self.assertNotIn("No conflict", page.get_text())
        self.assertIn("No conflict", self._page(self.path).get_text())

        detail = self.client.get("/documents/contacts/first")
        section = BeautifulSoup(detail.data, "html.parser").select_one('section[aria-labelledby="birthday-review"]')
        self.assertIn("Fixture source one", section.get_text())
        self.assertIn("Fixture source two", section.get_text())
        self.assertNotIn("Private source", section.get_text())

        # Choose a correction explicitly. A dated row and yearless row on the
        # same day are compatible; no merge, inferred year or other write.
        update = self.client.post("/documents/contacts", headers=self._csrf_headers(self.client),
                                  data={"contact_id": "second", "display_name": "Synthetic second",
                                        "email": mail, "birthday": "2000-05-01"})
        self.assertEqual(302, update.status_code)
        self.assertTrue(update.headers["Location"].endswith("/documents/contacts/second"))
        self.assertEqual("Fixture source two", store.get("second", "owner")["source"]["provider"])
        page = self._page(self.path + "?view=review")
        self.assertIn("Keine widersprüchlichen Geburtstage gefunden.", page.get_text())
        self.assertEqual("--05-01", store.get("first", "owner")["fields"]["birthday"])

    def test_review_filter_stays_selected_when_paginating(self):
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        for i in range(51):
            store.upsert({"display_name": f"Fixture {i:02}", "email": "group@example.test",
                          "birthday": f"--05-{1 + (i % 2):02}"}, "owner", f"contact-{i:02}")
        store.upsert({"display_name": "Outside review", "birthday": "--05-04"}, "owner", "plain")
        first = self._page(self.path + "?view=review")
        self.assertEqual(50, len(first.select("main tbody tr")))
        next_link = first.select_one('a[rel="next"]')["href"]
        self.assertIn("view=review", next_link)
        last = self._page(next_link)
        self.assertEqual(1, len(last.select("main tbody tr")))
        self.assertIn("view=review", last.select_one('a[rel="prev"]')["href"])
        self.assertNotIn("Outside review", last.get_text())
