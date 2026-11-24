"""Browser coverage: in-preview search, match navigation and copy actions."""
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


class DocumentPreviewBrowserTests(unittest.TestCase):
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
        base = Path(self.temp.name)
        app.config.update(
            DATABASE=str(base / "documents.sqlite"),
            DOCUMENT_ROOT=str(base / "documents"), TESTING=True,
            TEST_CSRF_PROTECTION=False, ALLOW_PUBLIC_REGISTRATION="1",
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) VALUES (?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("preview-tester", hash_password("preview-tester-password"), "Preview tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "preview-tester", "password": "preview-tester-password",
        })
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        store = DocumentStore(base / "documents")
        store.initialize()
        document = store.import_upload(
            io.BytesIO("Preview search finds Kirsche twice: Kirsche.\nSecond line without.\n".encode("utf-8")),
            "obst.txt", "preview-tester",
        )
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

    def test_search_highlights_navigates_and_copies(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    permissions=["clipboard-read", "clipboard-write"],
                )
                context.add_cookies([{
                    "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                    "value": self.cookie, "url": self.origin,
                }])
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.origin + f"/documents/{self.document_id}", wait_until="domcontentloaded")
                search = page.locator("[data-text-preview-search]")
                search.fill("Kirsche")
                page.wait_for_function(
                    "() => document.querySelectorAll('[data-preview-match]').length === 2",
                    timeout=5000,
                )
                self.assertEqual("1 / 2", page.locator("[data-text-preview-count]").inner_text())
                page.locator("[data-text-preview-next]").click()
                self.assertEqual("2 / 2", page.locator("[data-text-preview-count]").inner_text())
                search.press("Enter")
                self.assertEqual("1 / 2", page.locator("[data-text-preview-count]").inner_text())
                search.press("Shift+Enter")
                self.assertEqual("2 / 2", page.locator("[data-text-preview-count]").inner_text())
                page.locator("[data-text-preview-copy]").click()
                page.wait_for_function(
                    "() => document.querySelector('[data-text-preview-feedback]').textContent.length > 0",
                    timeout=5000,
                )
                pasted = page.evaluate("() => navigator.clipboard.readText()")
                self.assertIn("Kirsche", pasted)
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
