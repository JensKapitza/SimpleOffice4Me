"""Browser coverage: mobile quick-access bar and action button behavior."""
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
from app.password_security import hash_password


class MobileWorkspaceBrowserTests(unittest.TestCase):
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
                ("mobile-tester", hash_password("mobile-tester-password"), "Mobile tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "mobile-tester", "password": "mobile-tester-password",
        })
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
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

    def test_mobile_bar_navigates_with_one_tap(self):
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
                page.goto(self.origin + "/documents/", wait_until="domcontentloaded")
                bar = page.locator("[data-mobile-bar]")
                self.assertTrue(bar.is_visible())
                self.assertEqual(5, bar.locator("a[data-mobile-link]").count())
                with page.expect_navigation(wait_until="domcontentloaded"):
                    bar.locator('a[data-mobile-link="/documents/calendar"]').tap()
                self.assertIn("/documents/calendar", page.url)
                self.assertEqual(
                    "page", bar.locator('a[data-mobile-link="/documents/calendar"]').get_attribute("aria-current"),
                )
                box = bar.locator("a[data-mobile-link]").first.bounding_box()
                self.assertGreaterEqual(box["height"], 44)
            finally:
                context.close()
                browser.close()

    def test_fab_expands_contextual_actions(self):
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
                page.goto(self.origin + "/tasks/", wait_until="domcontentloaded")
                toggle = page.locator("[data-mobile-fab-toggle]")
                self.assertTrue(toggle.is_visible())
                menu = page.locator("[data-mobile-fab-menu]")
                self.assertFalse(menu.is_visible())
                toggle.tap()
                self.assertTrue(menu.is_visible())
                self.assertEqual("true", toggle.get_attribute("aria-expanded"))
                actions = menu.locator("[data-mobile-action]:not([hidden])")
                self.assertGreaterEqual(actions.count(), 3)
                first = actions.first.get_attribute("data-mobile-action")
                self.assertEqual("task", first)
                page.keyboard.press("Escape")
                self.assertFalse(menu.is_visible())
            finally:
                context.close()
                browser.close()

    def test_desktop_hides_mobile_workspace(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(browser, viewport={"width": 1280, "height": 900})
                page = context.new_page()
                page.goto(self.origin + "/documents/", wait_until="domcontentloaded")
                self.assertFalse(page.locator("[data-mobile-bar]").is_visible())
                self.assertFalse(page.locator("[data-mobile-fab-toggle]").is_visible())
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
