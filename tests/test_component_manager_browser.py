"""Authenticated Chromium layout checks for the optional component catalog."""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from app import db as database


class ComponentManagerBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None or not shutil.which("chromium"):
            raise unittest.SkipTest("Playwright and system Chromium are required")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION", "ALLOW_PUBLIC_REGISTRATION",
        )}
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "components.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True, TEST_CSRF_PROTECTION=False, ALLOW_PUBLIC_REGISTRATION="1",
        )
        with app.app_context():
            database.ensure_auth_database()
        client = app.test_client()
        reply = client.post("/auth/register", data={
            "username": "component-browser", "password": "component-browser-test-password",
        })
        self.assertEqual(302, reply.status_code)
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

    def test_component_status_grid_fits_mobile_and_desktop(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("chromium"),
                                                 headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(viewport={"width": 360, "height": 800},
                                              is_mobile=True, has_touch=True)
                page = context.new_page()
                page.goto(self.origin + "/auth/login", wait_until="domcontentloaded")
                page.locator("#username").fill("component-browser")
                page.locator("#password").fill("component-browser-test-password")
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.locator("form:has(#password) button[type='submit']").tap()
                page.goto(self.origin + "/setup/optional", wait_until="domcontentloaded")
                page.locator("#component-manager").wait_for(state="visible")
                self.assertTrue(page.locator("form[action='/setup/optional/install-all'] button").is_visible())
                mobile = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    page: document.documentElement.scrollWidth,
                    manager: document.querySelector('#component-manager').getBoundingClientRect().width,
                    cards: [...document.querySelectorAll('#component-manager .row > div')]
                      .map((card) => ({width: card.getBoundingClientRect().width,
                                      left: Math.round(card.getBoundingClientRect().left) }))
                })""")
                self.assertLessEqual(mobile["page"], mobile["viewport"], mobile)
                self.assertTrue(all(card["width"] > 0 and card["width"] <= mobile["viewport"]
                                    for card in mobile["cards"]), mobile)
                self.assertEqual(1, len({card["left"] for card in mobile["cards"]}), mobile)

                page.set_viewport_size({"width": 1280, "height": 900})
                desktop = page.evaluate("""() => ({
                    viewport: document.documentElement.clientWidth,
                    page: document.documentElement.scrollWidth,
                    cards: [...document.querySelectorAll('#component-manager .row > div')]
                      .map((card) => Math.round(card.getBoundingClientRect().left))
                })""")
                self.assertLessEqual(desktop["page"], desktop["viewport"], desktop)
                self.assertGreaterEqual(len(set(desktop["cards"])), 3, desktop)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
