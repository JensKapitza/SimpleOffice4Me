"""Open, inspect and correct a birthday conflict in a narrow real browser."""
import importlib.util
import os
from threading import Thread
import unittest

from werkzeug.serving import make_server

from app import app
from app.contact_store import ContactStore
from tests.http_api_fixture import PublicHttpTestCase


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install Playwright")
class ContactBirthdayReviewBrowserTests(PublicHttpTestCase):
    def test_review_at_phone_width(self):
        from playwright.sync_api import Error, expect, sync_playwright

        store = ContactStore(app.config["DOCUMENT_ROOT"])
        for ident, name, birthday, source in (
            ("one", "Synthetischer Kontakt", "--04-05", "Quelle A"),
            ("two", "Synthetischer Zweitkontakt", "1988-04-06", "Quelle B"),
        ):
            store.upsert({"display_name": name, "email": "same@example.test", "birthday": birthday},
                         "owner", ident, {"provider": source})
        with self.client.session_transaction() as state:
            state["simpleoffice_language"] = "de"
        session = app.config["SESSION_COOKIE_NAME"]
        origin_path = "/documents/contacts/birthdays"
        with make_server("127.0.0.1", 0, app, threaded=True) as server, sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(
                    executable_path=os.environ.get("SIMPLEOFFICE_TEST_CHROMIUM") or None,
                )
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("install Playwright Chromium")
                raise
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                context = browser.new_context(viewport={"width": 360, "height": 800},
                                              is_mobile=True, has_touch=True, service_workers="block")
                context.add_cookies([{"name": session, "value": self.client.get_cookie(session).value,
                                      "url": origin}])
                page = context.new_page()
                page.set_default_timeout(10000)
                page.goto(origin + origin_path)
                page.get_by_role("link", name="Zu prüfen (2)").tap()
                expect(page).to_have_url(origin + origin_path + "?view=review")
                page.get_by_role("link", name="Synthetischer Zweitkontakt", exact=True).first.tap()
                warning = page.get_by_role("region", name="Geburtstag prüfen")
                for line in ("--04-05", "1988-04-06", "Quelle A", "Quelle B"):
                    expect(warning).to_contain_text(line)
                page.locator("form[data-company-edit-form] #contact-birthday").fill("1988-04-05")
                page.get_by_role("button", name="Änderungen speichern").tap()
                expect(warning).to_have_count(0)
                page.goto(origin + origin_path + "?view=review")
                expect(page.get_by_text("Keine widersprüchlichen Geburtstage gefunden.")).to_be_visible()
                self.assertEqual("--04-05", store.get("one", "owner")["fields"]["birthday"])
            finally:
                browser.close()
                server.shutdown()
                thread.join(timeout=5)
