"""Browser coverage: calendar day quick-create and mobile list layout."""
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
from app.calendar_store import CalendarStore
from app.password_security import hash_password


class CalendarQuickCreateBrowserTests(unittest.TestCase):
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
                ("calendar-tester", hash_password("calendar-tester-password"), "Calendar tester"),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={
            "username": "calendar-tester", "password": "calendar-tester-password",
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

    def test_day_quick_add_prefills_booking_form(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(browser, viewport={"width": 1280, "height": 900})
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.origin + "/documents/calendar?month=2026-11", wait_until="domcontentloaded")
                button = page.locator('[data-calendar-quick-add="2026-11-19"]').first
                button.click()
                pane = page.locator('[data-calendar-pane="booking"]')
                self.assertTrue(pane.is_visible())
                start = pane.locator('#calendar-actions input[name="start"]')
                self.assertEqual("2026-11-19T09:00", start.input_value())
                end = pane.locator('#calendar-actions input[name="end"]')
                self.assertEqual("2026-11-19T10:00", end.input_value())
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()

    def test_month_grid_becomes_list_on_phones(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(
                    browser, viewport={"width": 360, "height": 800},
                    is_mobile=True, has_touch=True,
                )
                page = context.new_page()
                page.goto(self.origin + "/documents/calendar?month=2026-11", wait_until="domcontentloaded")
                self.assertFalse(page.locator(".calendar-weekday").first.is_visible())
                first_day = page.locator(".calendar-day:not(.empty)").first
                self.assertTrue(first_day.is_visible())
                box = first_day.bounding_box()
                viewport = page.viewport_size
                self.assertGreaterEqual(box["width"], viewport["width"] * 0.8)
                quick_add = first_day.locator("[data-calendar-quick-add]")
                add_box = quick_add.bounding_box()
                self.assertGreaterEqual(add_box["height"], 44)
                self.assertGreaterEqual(add_box["width"], 44)
            finally:
                context.close()
                browser.close()


    def test_today_activates_calendar_pane_and_scrolls_on_a_phone(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(browser, viewport={"width": 360, "height": 800},
                                        is_mobile=True, has_touch=True)
                page = context.new_page()
                page.goto(self.origin + "/documents/calendar?month=2020-01", wait_until="domcontentloaded")
                page.evaluate("sessionStorage.setItem('simpleoffice.calendar.workspace', 'booking')")
                page.locator('a[href$="#calendar-today"]').click()
                page.wait_for_url("**/documents/calendar#calendar-today")
                target = page.locator('#calendar-today[aria-current="date"]')
                target.wait_for(state="visible")
                self.assertTrue(page.locator('[data-calendar-pane="calendar"]').is_visible())
                self.assertTrue(page.locator('[data-calendar-pane="booking"]').is_hidden())
                page.wait_for_function("""() => {
                    const rect = document.getElementById('calendar-today').getBoundingClientRect();
                    return rect.top >= 0 && rect.bottom < window.innerHeight;
                }""")
            finally:
                context.close()
                browser.close()

    def test_phone_creates_appointment_followup_and_link_reopens_source(self):
        event = CalendarStore(app.config["DOCUMENT_ROOT"]).add(
            "Planning appointment", "Private fixture note", "2026-11-19T09:00",
            "2026-11-19T10:00", "", "calendar-tester",
        )
        event_id = event["event_id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            try:
                context = self._context(browser, viewport={"width": 390, "height": 844},
                                        is_mobile=True, has_touch=True)
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(self.origin + f"/documents/calendar?month=2026-11#calendar-event-{event_id}",
                          wait_until="domcontentloaded")
                modal = page.locator(f"#calendar-event-{event_id}")
                modal.wait_for(state="visible")
                form = modal.locator("[data-calendar-followup]")
                form.locator("xpath=../summary").click()
                self.assertEqual(0, form.locator("[data-appointment-context]").count())
                form.locator("input[name=due]").fill("2026-11-23")
                form.locator('button[type=submit]').click()
                page.wait_for_url("**/tasks/#task-*")
                self.assertEqual(0, page.get_by_text("Private fixture note", exact=True).count())
                page.locator("[data-task-appointment-link]").click()
                page.locator(f"#calendar-event-{event_id}").wait_for(state="visible")
                self.assertEqual(0, page.locator("[data-calendar-followup]").count())
                self.assertEqual([], errors)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
