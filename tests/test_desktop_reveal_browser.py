"""Browser coverage: reveal button appears only with a desktop bridge."""
from __future__ import annotations

import io
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
from app.document_store import DocumentStore
from app.password_security import hash_password


class DesktopRevealBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None or not shutil.which("chromium"):
            raise unittest.SkipTest("Playwright and system Chromium are required")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "DESKTOP_REVEAL_TOKEN", "TEST_CSRF_PROTECTION",
            "ALLOW_PUBLIC_REGISTRATION",
        )}
        base = Path(self.temp.name)
        app.config.update(
            DATABASE=str(base / "documents.sqlite"),
            DOCUMENT_ROOT=str(base / "documents"), TESTING=True,
            DESKTOP_REVEAL_TOKEN="browser-test-desktop-capability",
            TEST_CSRF_PROTECTION=False, ALLOW_PUBLIC_REGISTRATION="1",
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) VALUES (?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("reveal-tester", hash_password("reveal-tester-password"), "Reveal tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "reveal-tester", "password": "reveal-tester-password",
        })
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        store = DocumentStore(base / "documents")
        store.initialize()
        document = store.import_upload(io.BytesIO(b"reveal me"), "reveal.txt", "reveal-tester")
        self.document_id = document["document_id"]
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

    def test_reveal_button_stays_hidden_without_bridge(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(browser, viewport={"width": 1280, "height": 900})
                page = context.new_page()
                page.goto(self.origin + f"/documents/{self.document_id}", wait_until="domcontentloaded")
                self.assertFalse(page.locator("[data-desktop-reveal]").is_visible())
            finally:
                context.close()
                browser.close()

    def test_reveal_button_calls_bridge_with_opaque_document_id(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(browser, viewport={"width": 1280, "height": 900})
                context.add_init_script(
                    "window.simpleOfficeDesktop = {desktop: true, platform: 'linux',"
                    " revealed: [], revealDocument(id) { this.revealed.push(id); return Promise.resolve({ok: true}); }};"
                )
                page = context.new_page()
                page.goto(self.origin + f"/documents/{self.document_id}", wait_until="domcontentloaded")
                button = page.locator("[data-desktop-reveal]")
                self.assertTrue(button.is_visible())
                button.click()
                page.wait_for_function(
                    "() => (window.simpleOfficeDesktop.revealed || []).length === 1",
                    timeout=5000,
                )
                revealed = page.evaluate("() => window.simpleOfficeDesktop.revealed[0]")
                self.assertEqual(self.document_id, revealed)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
