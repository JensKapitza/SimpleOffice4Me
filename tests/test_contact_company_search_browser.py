"""Browser-level coverage for external company search and return-to-form state."""
from __future__ import annotations

import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from werkzeug.serving import make_server

from app import app
from app import db as database
from app.contact_store import ContactStore


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class CompanySearchBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "browser-user", "password": "browser-test-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "browser-user", "password": "browser-test-password"},
        )
        self.store = ContactStore(self.root)
        cookie = self.client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_desktop_android_and_iphone_safari_search_return_flow(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            scenarios = (
                (playwright.chromium, {}, "Desktop"),
                (playwright.chromium, playwright.devices["Pixel 7"], "Android"),
                (playwright.webkit, playwright.devices["iPhone 14"], "iPhone Safari"),
            )
            for browser_type, context_options, label in scenarios:
                with self.subTest(browser=label):
                    self._exercise_search_and_return(browser_type, context_options, label)

    def _exercise_search_and_return(self, browser_type, context_options, label):
        browser = browser_type.launch()
        context = browser.new_context(**context_options)
        context.add_cookies([{
            "name": app.config.get("SESSION_COOKIE_NAME", "session"),
            "value": self.cookie,
            "url": self.base_url,
        }])
        referrers = []

        def mock_search(route):
            referrers.append(route.request.headers.get("referer"))
            route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    "<!doctype html><meta charset=utf-8><title>Mock search</title>"
                    f'<pre id="company-result">Name: Browser Works {label} GmbH\n'
                    f'Firma: Browser Works {label} GmbH\nStraße: Teststraße\n'
                    "Hausnummer: 8\nPLZ: 10115\nOrt: Berlin\n"
                    "E-Mail: office@example.test</pre>"
                ),
            )

        context.route("https://www.google.com/**", mock_search)
        try:
            page = context.new_page()
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{self.base_url}/documents/contacts")
            page.locator("[data-company-data-search] summary").click()
            page.locator("[data-company-query-name]").fill(f"Browser Works {label} GmbH")
            page.locator("[data-company-query-city]").fill("Berlin")

            with page.expect_popup() as popup_info:
                page.locator('[data-company-provider="googleSearch"]').click()
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            self.assertIn("google.com/search?", popup.url)
            self.assertEqual(
                f"Browser Works {label} GmbH Berlin",
                parse_qs(urlsplit(popup.url).query)["q"][0],
            )
            copied_text = popup.locator("#company-result").inner_text()
            popup.close()
            page.bring_to_front()

            # Returning from the external tab must preserve the original form draft.
            self.assertEqual(f"Browser Works {label} GmbH", page.locator("[data-company-query-name]").input_value())
            self.assertEqual("Berlin", page.locator("[data-company-query-city]").input_value())
            self.assertEqual(popup.url, page.locator("[data-company-source-url]").input_value())
            self.assertEqual(1, page.locator("[data-company-data-source]").count())
            self.assertEqual(1, len(referrers))
            self.assertEqual([], [value for value in referrers if value])

            page.locator("[data-company-paste]").fill(copied_text)
            page.locator("[data-company-parse]").click()
            self.assertEqual(
                f"Browser Works {label} GmbH",
                page.locator('[data-company-value="company"]').input_value(),
            )
            page.locator("[data-company-preview-button]").click()
            page.locator("[data-company-preview]").wait_for(state="visible")
            selected_fields = page.locator("[data-company-include]:checked:not(:disabled)")
            self.assertGreater(selected_fields.count(), 0)
            apply_button = page.locator("[data-company-apply]")
            apply_button.click()
            source_value = page.locator("[data-company-data-source]").input_value()
            status_text = page.locator("[data-company-assist-status]").inner_text()
            self.assertTrue(
                source_value,
                f"No source recorded after apply: status={status_text!r}; "
                f"selected={selected_fields.count()}; page_errors={page_errors}",
            )
            self.assertFalse(any(
                contact["fields"].get("company") == f"Browser Works {label} GmbH"
                for contact in self.store.contacts("browser-user")
            ))

            self.assertEqual(
                "https://www.google.com/search",
                source_value.split("?", 1)[0],
            )
            page.locator('#contact-create-form button[type="submit"]').click()
            saved = next(
                contact for contact in self.store.contacts("browser-user")
                if contact["fields"].get("company") == f"Browser Works {label} GmbH"
            )
            self.assertEqual(f"Browser Works {label} GmbH", saved["fields"]["display_name"])
            self.assertIn("Teststraße 8", [address["value"] for address in saved["addresses"]])
            self.assertEqual(
                "https://www.google.com/search/",
                saved["external_data_sources"][-1]["url"],
            )
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
