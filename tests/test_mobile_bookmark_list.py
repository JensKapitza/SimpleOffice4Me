"""Real Chromium regression coverage for the authenticated bookmark list.

Run with: python -m unittest tests.test_mobile_bookmark_list
The test uses a temporary local database and the system Chromium executable.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Keep the regular unit suite usable without browser extras.
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from app import db as database
from app.settings_store import SettingsStore


class MobileBookmarkListBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None or not shutil.which("chromium"):
            raise unittest.SkipTest("Playwright and system Chromium are required")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION", "ALLOW_PUBLIC_REGISTRATION")
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "bookmarks.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            ALLOW_PUBLIC_REGISTRATION="1",
        )
        settings_store = SettingsStore(app.config["DOCUMENT_ROOT"])
        settings = settings_store.settings()
        settings["interface"]["default_language"] = "de"
        settings_store.save(settings, "browser-fixture")
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        response = self.client.post("/auth/register", data={
            "username": "bookmark-browser",
            "password": "mobile-bookmark-test-password",
        })
        self.assertEqual(302, response.status_code)
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.thread.join(timeout=5)
        app.config.update(self.saved)
        self.temp.cleanup()

    def _login(self, page):
        page.goto(self.origin + "/auth/login", wait_until="domcontentloaded")
        page.locator("#username").fill("bookmark-browser")
        page.locator("#password").fill("mobile-bookmark-test-password")
        page.locator("form:has(#password) button[type='submit']").click()
        page.wait_for_load_state("domcontentloaded")
        page.goto(self.origin + "/bookmarks", wait_until="domcontentloaded")
        self.assertTrue(page.get_by_role("heading", name="Lesezeichen", exact=True).is_visible())

    def test_mobile_long_url_and_touch_flows_and_desktop_layout(self):
        long_url = "https://example.org/research/" + "segment-" * 32 + "?source=mobile&view=full"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"),
                headless=True,
                args=["--no-sandbox"],
            )
            try:
                context = browser.new_context(viewport={"width": 360, "height": 800}, is_mobile=True, has_touch=True)
                page = context.new_page()
                self._login(page)

                page.locator("#bookmark-title").fill("Mobile research source")
                page.locator("#bookmark-url").fill(long_url)
                page.locator("#bookmark-folder").fill("Research/" + "folder" * 16)
                page.locator("#bookmark-tags").fill("mobiletag" * 12)
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.locator("form[action='/bookmarks/add'] button[type='submit']").tap()
                item = page.locator("article.list-group-item").filter(has=page.locator("input[value='Mobile research source']"))
                self.assertEqual(1, item.count())
                self.assertEqual(long_url, item.locator("input[name='url']").input_value())

                # The narrow viewport must not require page-level horizontal scrolling.
                overflow = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    page: document.documentElement.scrollWidth,
                    card: (() => { const e = document.querySelector('article.list-group-item'); return e.scrollWidth; })()
                })""")
                self.assertLessEqual(overflow["page"], overflow["viewport"], overflow)
                self.assertLessEqual(overflow["card"], overflow["viewport"], overflow)

                # Search, edit and pin remain usable by a touch interaction.
                page.locator("#bookmark-search").fill("Mobile research")
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.locator(".bookmark-search button[type='submit']").tap()
                item = page.locator("article.list-group-item").filter(has=page.locator("input[value='Mobile research source']"))
                self.assertEqual(1, item.count())
                item.locator("input[name='title']").fill("Edited research source")
                with page.expect_navigation(wait_until="domcontentloaded"):
                    item.locator(".bookmark-edit-actions button[type='submit']").tap()
                item = page.locator("article.list-group-item").filter(has=page.locator("input[value='Edited research source']"))
                self.assertEqual(1, item.count())
                with page.expect_navigation(wait_until="domcontentloaded"):
                    item.locator(".bookmark-item-actions form:first-child button").tap()
                item = page.locator("article.list-group-item").filter(has=page.locator("input[value='Edited research source']"))
                self.assertEqual("Lösen", item.locator(".bookmark-item-actions form:first-child button").inner_text())

                # Verify the actions are not clipped and can be tapped at 360 px.
                for label, action in (
                    ("Änderungen speichern", item.locator(".bookmark-edit-actions button")),
                    ("Öffnen", item.locator(".bookmark-edit-actions a")),
                    ("Lösen", item.locator(".bookmark-item-actions form:first-child button")),
                    ("Löschen", item.locator(".bookmark-item-actions form:last-child button")),
                ):
                    box = action.bounding_box()
                    self.assertIsNotNone(box, label)
                    self.assertGreaterEqual(box["width"], 32, label)
                    self.assertGreaterEqual(box["height"], 44, label)
                    self.assertGreaterEqual(box["x"], 0, label)
                    self.assertLessEqual(box["x"] + box["width"], 360, label)
                search_button = page.locator(".bookmark-search button")
                self.assertGreaterEqual(search_button.bounding_box()["height"], 44)

                # Delete is a normal existing flow and removes the item from the list.
                with page.expect_navigation(wait_until="domcontentloaded"):
                    item.locator(".bookmark-item-actions form:last-child button").tap()
                self.assertTrue(page.get_by_text("Noch keine Lesezeichen gespeichert.", exact=True).is_visible())
                context.close()

                desktop = browser.new_page(viewport={"width": 1280, "height": 900})
                self._login(desktop)
                self.assertLessEqual(
                    desktop.evaluate("document.documentElement.scrollWidth"),
                    desktop.evaluate("document.documentElement.clientWidth"),
                )
                self.assertTrue(desktop.get_by_role("heading", name="Lesezeichen", exact=True).is_visible())
                desktop.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
