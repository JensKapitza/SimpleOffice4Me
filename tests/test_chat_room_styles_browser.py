"""Authenticated chat room browser coverage for its local responsive stylesheet."""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from werkzeug.serving import make_server

from app import app
from app import db as database
from app.chat_store import ChatStore


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright browser extra is required")
class ChatRoomStylesBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(app.config.update, self.saved)
        app.config.update(
            TESTING=True,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        client = app.test_client()
        client.post("/auth/register", data={"username": "chat-style-browser", "password": "chat-style-browser-password"})
        client.post("/auth/login", data={"username": "chat-style-browser", "password": "chat-style-browser-password"})
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        room = ChatStore(self.root).create_room("Browser styles", "chat-style-browser", [])
        self.room_url = f"/chat/rooms/{room['room_id']}"
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)

    def test_authenticated_room_loads_and_applies_chat_css_on_desktop_and_phone(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = shutil.which("chromium") or (
                playwright.chromium.executable_path
                if Path(playwright.chromium.executable_path).is_file()
                else None
            )
            if not executable:
                self.skipTest("Chromium is not installed")
            browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
            try:
                context = browser.new_context(
                    viewport={"width": 1365, "height": 900},
                    is_mobile=True,
                    has_touch=True,
                )
                context.add_cookies([{
                    "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                    "value": self.cookie,
                    "url": self.base_url,
                }])
                page = context.new_page()
                css_responses = []
                page.on("response", lambda response: css_responses.append(response) if response.url.endswith("/static/css/chat.css") else None)
                response = page.goto(self.base_url + self.room_url, wait_until="networkidle")
                self.assertEqual(200, response.status)
                self.assertIn("Browser styles", page.locator("h1").inner_text())
                self.assertTrue(page.locator("#chat-compose").is_visible())
                self.assertEqual(1, len(css_responses))
                self.assertEqual(200, css_responses[0].status)
                self.assertIn("chat-message-scroll", css_responses[0].text())

                desktop = page.locator("#chat-scroll").evaluate("el => ({maxHeight: getComputedStyle(el).maxHeight, minHeight: getComputedStyle(el).minHeight})")
                self.assertAlmostEqual(548, float(desktop["maxHeight"].removesuffix("px")), delta=1)
                self.assertEqual("288px", desktop["minHeight"])

                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#chat-body").fill("Mobile browser regression message")
                page.locator('#chat-compose button[type="submit"]').click()
                page.wait_for_load_state("networkidle")
                self.assertIn("Mobile browser regression message", page.locator("#chat-messages").inner_text())
                mobile = page.locator("#chat-scroll").evaluate("el => ({maxHeight: getComputedStyle(el).maxHeight, minHeight: getComputedStyle(el).minHeight})")
                self.assertAlmostEqual(844 * 0.38, float(mobile["maxHeight"].removesuffix("px")), delta=1)
                self.assertAlmostEqual(min(160, 844 * 0.30), float(mobile["minHeight"].removesuffix("px")), delta=1)
                composer = page.locator("#chat-compose").evaluate("el => ({position: getComputedStyle(el).position, bottom: getComputedStyle(el).bottom})")
                self.assertEqual("sticky", composer["position"])
                # Catch cascade regressions: Bootstrap's .bottom-0 is !important,
                # so the safe-area declaration must also be important to take effect.
                bottom_priority = page.evaluate("""() => {
                  const findBottom = rules => {
                    for (const rule of rules || []) {
                      if (rule.selectorText === '#chat-compose') {
                        const bottom = rule.style.getPropertyValue('bottom');
                        if (bottom.includes('safe-area-inset-bottom'))
                          return rule.style.getPropertyPriority('bottom');
                      }
                      if (rule.cssRules) {
                        const nested = findBottom(rule.cssRules);
                        if (nested !== null) return nested;
                      }
                    }
                    return null;
                  };
                  for (const sheet of document.styleSheets) {
                    const priority = findBottom(sheet.cssRules);
                    if (priority !== null) return priority;
                  }
                  return null;
                }""")
                self.assertEqual("important", bottom_priority)
                self.assertEqual("0px", composer["bottom"])
            finally:
                browser.close()

    def test_mobile_incoming_message_keeps_scroll_and_button_jumps_to_bottom(self):
        from playwright.sync_api import sync_playwright

        store = ChatStore(self.root)
        room = store.upsert_remote_room(
            str(uuid.uuid4()), "Incoming message browser test", "remote-peer",
            ["chat-style-browser"], ["remote-friend"],
        )
        self.room_url = f"/chat/rooms/{room['room_id']}"
        timestamp = 1_800_000_000
        for index in range(24):
            store.upsert_remote_message(
                room["room_id"], "remote-peer", "remote-friend", str(uuid.uuid4()),
                f"Earlier incoming message {index}: " + ("long mobile chat content " * 12),
                created_at=timestamp + index,
            )

        with sync_playwright() as playwright:
            executable = shutil.which("chromium") or (
                playwright.chromium.executable_path
                if Path(playwright.chromium.executable_path).is_file()
                else None
            )
            if not executable:
                self.skipTest("Chromium is not installed")
            browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
            try:
                context = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    is_mobile=True,
                    has_touch=True,
                )
                context.add_cookies([{
                    "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                    "value": self.cookie,
                    "url": self.base_url,
                }])
                page = context.new_page()
                response = page.goto(self.base_url + self.room_url, wait_until="networkidle")
                self.assertEqual(200, response.status)
                scroll = page.locator("#chat-scroll")
                page.wait_for_function("""() => {
                  const el = document.querySelector('#chat-scroll');
                  return el && el.scrollHeight > el.clientHeight;
                }""")
                scroll.evaluate("el => { el.scrollTop = el.scrollHeight / 2; }")
                before = scroll.evaluate("el => el.scrollTop")
                self.assertGreater(before, 140, "start in the middle of the history")

                store.upsert_remote_message(
                    room["room_id"], "remote-peer", "remote-friend", str(uuid.uuid4()),
                    "New incoming mobile message", created_at=timestamp + 100,
                )
                button = page.locator("#chat-new-messages")
                button.wait_for(state="visible", timeout=10000)
                self.assertIn("New incoming mobile message", page.locator("#chat-messages").inner_text())
                after_update = scroll.evaluate("el => el.scrollTop")
                self.assertAlmostEqual(before, after_update, delta=2, msg="polling must preserve the reader's scroll position")

                with self.subTest(control="touch target inside message pane"):
                    self.assertGreaterEqual(button.bounding_box()["height"], 44)
                    self.assertTrue(button.evaluate("""el => {
                      const box = el.getBoundingClientRect();
                      const pane = el.closest('section').getBoundingClientRect();
                      return box.top >= pane.top && box.bottom <= pane.bottom;
                    }"""))
                button.tap()
                page.wait_for_function("""() => {
                  const el = document.querySelector('#chat-scroll');
                  return el && el.scrollHeight - el.scrollTop - el.clientHeight < 5;
                }""")
                self.assertGreater(scroll.evaluate("el => el.scrollTop"), after_update)
                self.assertFalse(button.is_visible(), "the jump control should hide after reaching the newest message")
                context.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
