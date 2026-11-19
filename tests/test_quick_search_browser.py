"""Browser coverage: live quick search, keyboard navigation, preview and recents."""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Browser extras are optional for the regular unit suite.
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from app import db as database
from app.object_store import ObjectStore
from app.password_security import hash_password


class QuickSearchBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None or not shutil.which("chromium"):
            raise unittest.SkipTest("Playwright and system Chromium are required")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION",
            "ALLOW_PUBLIC_REGISTRATION",
        )}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "documents.sqlite"),
            DOCUMENT_ROOT=str(self.root), TESTING=True,
            TEST_CSRF_PROTECTION=False, ALLOW_PUBLIC_REGISTRATION="1",
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) VALUES (?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("search-browser", hash_password("search-browser-password"), "Search tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "search-browser", "password": "search-browser-password",
        })
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        store = ObjectStore(self.root)
        store.create({"name": "Live Test Alpha", "type": "Gerät"}, "search-browser")
        store.create({"name": "Live Test Beta", "type": "Gerät"}, "search-browser")
        store.create({"name": 'Live <script>alert("x")</script> Gamma', "type": "Gerät"}, "search-browser")
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

    def _context(self, browser, **kwargs):
        context = browser.new_context(**kwargs)
        context.add_cookies([{
            "name": app.config.get("SESSION_COOKIE_NAME", "session"),
            "value": self.cookie, "url": self.origin,
        }])
        return context

    def test_live_search_keyboard_preview_and_recents(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(browser, viewport={"width": 1280, "height": 900})
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.origin + "/documents/quick-search", wait_until="domcontentloaded")
                search = page.locator("#quick-search-query")
                search.fill("Live Test")
                page.wait_for_function(
                    "n => document.querySelectorAll('[data-quick-match]:not([hidden])').length >= n",
                    arg=2, timeout=8000,
                )
                self.assertNotIn("?q=", page.url)
                self.assertEqual([], page.locator("[data-quick-list] script").all())
                search.press("ArrowDown")
                active = page.locator("[data-quick-match].quick-card-active")
                self.assertEqual(1, active.count())
                with page.expect_navigation(wait_until="domcontentloaded"):
                    search.press("Enter")
                self.assertIn("/objects/", page.url)
                page.goto(self.origin + "/documents/quick-search", wait_until="domcontentloaded")
                recents = page.locator("[data-quick-recents]")
                self.assertTrue(recents.is_visible())
                self.assertIn("Live Test", recents.inner_text())
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()

    def test_preview_opens_without_page_change(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(
                    browser, viewport={"width": 390, "height": 844},
                    is_mobile=True, has_touch=True,
                )
                page = context.new_page()
                page.goto(self.origin + "/documents/quick-search?q=Live%20Test", wait_until="domcontentloaded")
                card = page.locator("[data-quick-match]").first
                card.click(position={"x": 20, "y": 20})
                preview = page.locator("[data-quick-preview]")
                preview.wait_for(state="visible", timeout=5000)
                self.assertTrue(preview.locator("[data-quick-preview-title]").inner_text().strip())
                page.locator("[data-quick-preview-close]").click()
                self.assertFalse(preview.is_visible())
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
