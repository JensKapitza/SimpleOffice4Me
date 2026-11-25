"""Authenticated pages for the private Android folder-source bridge."""
from __future__ import annotations

from bs4 import BeautifulSoup

from werkzeug.security import generate_password_hash

from app import app
from app.db import get_db
from tests.http_api_fixture import PublicHttpTestCase


class AndroidLocalDocumentSourcePageTests(PublicHttpTestCase):
    @staticmethod
    def rendered_scope(page):
        panel = BeautifulSoup(page.data, "html.parser").select_one("[data-local-folder-page]")
        return panel["data-android-folder-owner"]

    def test_folder_page_is_private_and_names_are_not_rendered_by_the_server(self):
        visitor = app.test_client()
        self.assertEqual(302, visitor.get("/documents/local-storage").status_code)
        first = self.client.get("/documents/local-storage")
        self.assertEqual(200, first.status_code)
        self.assertEqual("private, no-store", first.headers["Cache-Control"])
        self.assertRegex(self.rendered_scope(first), r"^[a-f0-9]{64}$")
        self.assertEqual(self.rendered_scope(first), self.rendered_scope(self.client.get("/documents/local-storage")))
        with app.app_context():
            database = get_db()
            database.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,0)",
                             ("other-profile", generate_password_hash("test-only-api-password")))
            database.commit()
        self._login(visitor, "other-profile")
        self.assertNotEqual(self.rendered_scope(first), self.rendered_scope(visitor.get("/documents/local-storage")))
        body = BeautifulSoup(first.data, "html.parser")
        self.assertTrue(body.select_one("#local-folder-search-panel").has_attr("hidden"))
        self.assertEqual([], body.select("#local-folder-search-results li"))

    def test_drive_diagnostics_are_fixed_android_statuses_not_provider_content(self):
        path = "/settings/google-drive"
        android_headers = {"User-Agent": "SimpleOffice4Me-Android/synthetic"}
        for status, phrase in (
            ("configuration", "de.simpleoffice4me.android"),
            ("offline", "konnte das Netzwerk nicht erreichen"),
            ("denied", "nicht erteilt"),
            ("play-services", "benötigen ein Update"),
        ):
            with self.subTest(status=status):
                query = {"android": status}
                mobile = self.client.get(path, query_string=query, headers=android_headers)
                self.assertEqual(200, mobile.status_code)
                self.assertIn(phrase, mobile.get_data(as_text=True))
                self.assertNotIn(phrase, self.client.get(path, query_string=query).get_data(as_text=True))
        unknown = "offline-not-a-provider-message"
        response = self.client.get(path, query_string={"android": unknown}, headers=android_headers)
        self.assertNotIn(unknown, BeautifulSoup(response.data, "html.parser").select_one("main").get_text())
