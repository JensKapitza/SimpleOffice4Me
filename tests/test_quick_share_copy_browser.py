"""Exercise quick-share copy behavior in real desktop and mobile Chromium."""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Keep non-browser test runs usable without Playwright extras.
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from app import db as database


class QuickShareCopyBrowserTests(unittest.TestCase):
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
            DATABASE=str(Path(self.temp.name) / "quick-share-copy.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"), TESTING=True,
            TEST_CSRF_PROTECTION=False, ALLOW_PUBLIC_REGISTRATION="1",
        )
        with app.app_context():
            database.ensure_auth_database()
        client = app.test_client()
        response = client.post("/auth/register", data={
            "username": "quick-share-copy", "password": "quick-share-copy-password",
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

    def _create_share(self, page, payload):
        page.goto(self.origin + "/auth/login", wait_until="domcontentloaded")
        page.locator("#username").fill("quick-share-copy")
        page.locator("#password").fill("quick-share-copy-password")
        page.locator("form:has(#password) button[type='submit']").click()
        page.wait_for_load_state("domcontentloaded")
        page.goto(self.origin + "/quick-share", wait_until="domcontentloaded")
        page.locator("#quick-share-text").fill(payload)
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.locator("#quick-share-form button[type='submit']").click()
        button = page.locator("[data-copy-value]")
        self.assertTrue(button.is_visible())
        self.assertEqual(payload, button.get_attribute("data-copy-value"))
        self.assertEqual(payload, page.locator("[data-qr-payload]").text_content())
        return button

    def test_clipboard_success_and_manual_fallback_on_desktop_and_mobile(self):
        payload = "Copied exactly: café & tea\nSecond line <ready>"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                for label, viewport, mobile, touch in (
                    ("desktop", {"width": 1280, "height": 900}, False, False),
                    ("mobile", {"width": 360, "height": 800}, True, True),
                ):
                    with self.subTest(device=label):
                        context = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=touch)
                        context.add_init_script("""
                          Object.defineProperty(navigator, 'clipboard', {
                            configurable: true,
                            value: { writeText: async (value) => {
                              window.__copyCalls = (window.__copyCalls || []);
                              window.__copyCalls.push(value);
                              if (window.__copyMode === 'reject') throw new Error('denied');
                            } }
                          });
                        """)
                        page = context.new_page()
                        try:
                            button = self._create_share(page, payload)

                            original_label = button.inner_text()

                            # Successful copy uses the exact payload and provides feedback on each click.
                            page.evaluate("window.__copyMode = 'success'")
                            activate = button.tap if mobile else button.click
                            activate()
                            self.assertEqual([payload], page.evaluate("window.__copyCalls"))
                            status = page.locator("[data-copy-status]")
                            self.assertRegex(status.inner_text().casefold(), r"copied|kopiert")
                            activate()
                            self.assertEqual([payload, payload], page.evaluate("window.__copyCalls"))
                            self.assertRegex(status.inner_text().casefold(), r"copied|kopiert")

                            self.assertEqual(original_label, button.inner_text())

                            # A rejected Clipboard API call exposes selectable payload and manual instructions.
                            page.evaluate("window.__copyMode = 'reject'")
                            activate()
                            details = page.locator("[data-qr-payload]").locator("xpath=ancestor::details")
                            self.assertTrue(details.evaluate("node => node.open"))
                            self.assertTrue(page.locator("[data-qr-payload]").is_visible())
                            self.assertEqual(payload, page.evaluate("window.getSelection().toString()"))
                            self.assertRegex(status.inner_text().casefold(), r"kopier|copy|select")

                            # Missing clipboard support follows the same manual path on a subsequent click.
                            details.evaluate("node => { node.open = false; window.getSelection().removeAllRanges(); }")
                            page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:undefined})")
                            activate()
                            self.assertTrue(details.evaluate("node => node.open"))
                            self.assertEqual(payload, page.evaluate("window.getSelection().toString()"))
                        finally:
                            context.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
