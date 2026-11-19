"""Browser coverage: language subjects start cleanly and ask in their own language."""
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


class KidsLanguageSubjectsBrowserTests(unittest.TestCase):
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
                ("language-browser", hash_password("language-browser-password"), "Language tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "language-browser", "password": "language-browser-password",
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

    def _question_text(self, page) -> str:
        question = page.locator("[data-question]")
        return question.get_attribute("aria-label") or question.inner_text()

    def test_english_subject_starts_without_errors_and_asks_in_english(self):
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
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(self.origin + "/kids/lernen/", wait_until="domcontentloaded")
                page.locator('[data-subject="english"]').click()
                page.locator("[data-game]").wait_for(state="visible", timeout=5000)
                question = self._question_text(page)
                self.assertTrue(question.strip())
                self.assertRegex(question, r"Which|What|Listen|Tap|Say")
                self.assertNotIn("Welcher", question)
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()

    def test_german_subject_asks_in_german(self):
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
                page.goto(self.origin + "/kids/lernen/", wait_until="domcontentloaded")
                page.locator('[data-subject="german"]').click()
                page.locator("[data-game]").wait_for(state="visible", timeout=5000)
                question = self._question_text(page)
                self.assertRegex(question, r"Welch|Was|Wie")
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
