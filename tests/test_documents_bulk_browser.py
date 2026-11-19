"""Browser coverage: bulk select, bulk tag with progress, list updates."""
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


class DocumentsBulkBrowserTests(unittest.TestCase):
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
                ("bulk-tester", hash_password("bulk-tester-password"), "Bulk tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "bulk-tester", "password": "bulk-tester-password",
        })
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        store = DocumentStore(base / "documents")
        store.initialize()
        store.import_upload(io.BytesIO(b"bulk file one"), "eins.txt", "bulk-tester")
        store.import_upload(io.BytesIO(b"bulk file two"), "zwei.txt", "bulk-tester")
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

    def test_bulk_tag_flow_with_progress(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_cookies([{
                    "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                    "value": self.cookie, "url": self.origin,
                }])
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.origin + "/documents/", wait_until="domcontentloaded")
                bar = page.locator("[data-bulk-bar]")
                self.assertFalse(bar.is_visible())
                boxes = page.locator("[data-bulk-select]")
                self.assertGreaterEqual(boxes.count(), 2)
                boxes.nth(0).check()
                boxes.nth(1).check()
                self.assertTrue(bar.is_visible())
                self.assertIn("2", bar.locator("[data-bulk-count]").inner_text())
                bar.locator("[data-bulk-tags]").fill("stapel-test")
                with page.expect_navigation(wait_until="domcontentloaded"):
                    bar.locator("[data-bulk-tags-apply]").click()
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
