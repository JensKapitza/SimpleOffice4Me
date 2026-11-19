"""Browser coverage: maze typing mode playthrough and viewport fit on phones."""
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


class KidsMazeTypingBrowserTests(unittest.TestCase):
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
                ("maze-browser", hash_password("maze-browser-password"), "Maze tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "maze-browser", "password": "maze-browser-password",
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

    def _login_context(self, browser, **kwargs):
        context = browser.new_context(**kwargs)
        context.add_cookies([{
            "name": app.config.get("SESSION_COOKIE_NAME", "session"),
            "value": self.cookie, "url": self.origin,
        }])
        return context

    def test_typing_mode_completes_a_maze_round_by_letters(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._login_context(
                    browser, viewport={"width": 1280, "height": 900},
                )
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.origin + "/kids/lernen/?game=maze", wait_until="domcontentloaded")
                panel = page.locator("[data-maze-panel]")
                panel.wait_for(state="visible", timeout=5000)
                page.locator("[data-maze-typing]").click()
                self.assertEqual("true", page.locator("[data-maze-typing]").get_attribute("aria-pressed"))
                self.assertTrue(page.locator("[data-maze-letters]").is_visible())
                self.assertFalse(page.locator("[data-maze-controls]").is_visible())
                self.assertTrue(page.locator('[data-maze-direction="up"]').is_disabled())
                task = page.locator("[data-maze-task]")
                letter_button = lambda letter: page.locator("[data-maze-letters] button", has_text=letter).first
                # A wrong letter must not advance the word and must not break the game.
                expected = task.get_attribute("data-maze-expected")
                wrong = "Z" if expected != "Z" else "Q"
                letter_button(wrong).click()
                self.assertEqual(expected, task.get_attribute("data-maze-expected"))
                # Type every expected letter until the round completes.
                for _ in range(60):
                    if page.locator("[data-search-next]").is_visible():
                        break
                    wanted = task.get_attribute("data-maze-expected") or "A"
                    letter_button(wanted).click()
                self.assertTrue(page.locator("[data-search-next]").is_visible())
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()

    def test_maze_panel_fits_a_phone_viewport(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._login_context(
                    browser, viewport={"width": 360, "height": 800},
                    is_mobile=True, has_touch=True,
                )
                page = context.new_page()
                page.goto(self.origin + "/kids/lernen/?game=maze", wait_until="domcontentloaded")
                panel = page.locator("[data-maze-panel]")
                panel.wait_for(state="visible", timeout=5000)
                page.locator("[data-maze-typing]").click()
                panel.scroll_into_view_if_needed()
                box = panel.bounding_box()
                self.assertIsNotNone(box)
                self.assertLessEqual(box["y"] + box["height"], 800 + 1)
                maze_map = page.locator("[data-maze-map]").bounding_box()
                self.assertIsNotNone(maze_map)
                self.assertLessEqual(maze_map["x"] + maze_map["width"], 360 + 1)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
