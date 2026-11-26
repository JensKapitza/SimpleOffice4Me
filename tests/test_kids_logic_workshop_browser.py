"""Kids workshop from ordinary sign-in, desktop keyboard and portrait touch."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase

PATH = "/kids/lernen/logikwerkstatt"


class KidsLogicBrowserTests(PublicHttpTestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            message = "Playwright and its Chromium build are required"
            if os.getenv("SIMPLEOFFICE_LOGIC_BROWSER_REQUIRED") == "1":
                raise RuntimeError(message)
            raise unittest.SkipTest(message)

    def setUp(self):
        super().setUp()
        language = self.client.post("/documents/settings/language", data={"language": "en"},
                                    headers=self._csrf_headers(self.client))
        self.assertEqual(language.status_code, 302)
        cookie = self.client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.cookies = {"name": cookie.key, "value": cookie.value}
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def _stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def _open(self, browser, *, mobile=False):
        viewport = {"width": 360, "height": 800} if mobile else {"width": 1200, "height": 840}
        context = browser.new_context(viewport=viewport, has_touch=mobile, is_mobile=mobile,
                                      device_scale_factor=1, service_workers="block")
        context.add_cookies([{**self.cookies, "url": self.origin}])
        page = context.new_page()
        requests, errors = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append((request.method, request.url)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.origin + "/")
                   else route.abort())
        page.goto(self.origin + "/kids/", wait_until="domcontentloaded")
        return context, page, requests, errors

    @staticmethod
    def _press(parent, selector):
        target = parent.locator(selector)
        expect(target).to_be_enabled()
        target.focus()
        target.press("Space")

    def _no_network_side_effects(self, requests, errors):
        self.assertEqual(errors, [])
        self.assertTrue(all(method == "GET" and url.startswith(self.origin + "/") for method, url in requests), requests)
        self.assertFalse(any(("/sprache?" in url or "/voice?" in url) for _, url in requests), requests)

    def test_keyboard_circuit_interlock_and_no_skipped_applesauce_station(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                _context, page, requests, errors = self._open(browser)
                page.locator(f'a[href="{PATH}#schalter"]').press("Enter")
                circuit = page.locator('[data-logic-unit="circuit"]')
                switches = circuit.locator("[data-circuit-mode]")
                status = circuit.locator("[data-logic-status]")
                expect(status).to_contain_text("Lamp off")
                self._press(circuit, '[data-switch="a"]')
                expect(status).to_contain_text("Lamp on")
                switches.select_option("and")
                self._press(circuit, '[data-switch="a"]')
                expect(status).to_contain_text("Lamp off")
                self._press(circuit, '[data-switch="b"]')
                expect(status).to_contain_text("Lamp on")
                switches.select_option("or")
                self._press(circuit, '[data-switch="a"]')
                expect(status).to_contain_text("Lamp on")
                self._press(circuit, '[data-switch="b"]')
                expect(status).to_contain_text("Lamp on")
                switches.select_option("not")
                expect(circuit.locator("[data-circuit-rule]")).to_contain_text("not an open switch")
                expect(status).to_contain_text("Lamp on")
                self._press(circuit, '[data-switch="a"]')
                expect(status).to_contain_text("Lamp off")

                gate = page.locator('[data-logic-unit="gate"]')
                self._press(gate, '[data-switch="up"]')
                self._press(gate, '[data-switch="down"]')
                self._press(gate, "[data-step]")
                expect(gate.locator("[data-position]")).to_have_attribute("data-position", "0")
                expect(gate.locator("[data-logic-status]")).to_contain_text("both on")
                self._press(gate, '[data-switch="down"]')
                for _ in range(3):
                    self._press(gate, "[data-step]")
                expect(gate.locator("[data-position]")).to_have_attribute("data-position", "2")
                expect(gate.locator("[data-logic-status]")).to_contain_text("upper limit switch")
                self._press(gate, '[data-switch="up"]')
                self._press(gate, '[data-switch="down"]')
                for _ in range(3):
                    self._press(gate, "[data-step]")
                expect(gate.locator("[data-position]")).to_have_attribute("data-position", "0")
                expect(gate.locator("[data-logic-status]")).to_contain_text("lower limit switch")

                apple = page.locator('[data-logic-unit="apple"]')
                apple_status = apple.locator("[data-logic-status]")
                self._press(apple, '[data-switch="filling"]')
                self._press(apple, "[data-step]")
                expect(apple.locator('[aria-current="step"]')).to_have_attribute("data-station", "left")
                expect(apple_status).to_contain_text("waiting on the left")
                self._press(apple, '[data-switch="belt"]')
                self._press(apple, "[data-step]")
                expect(apple_status).to_contain_text("Turn on the press")
                self._press(apple, '[data-switch="processing"]')
                self._press(apple, "[data-step]")
                expect(apple_status).to_contain_text("empty jar")
                self._press(apple, "[data-step]")
                expect(apple.locator('[aria-current="step"]')).to_have_attribute("data-station", "sauce")
                self._press(apple, '[data-switch="glass"]')
                self._press(apple, "[data-step]")
                expect(apple_status).to_contain_text("jar of applesauce")
                self._press(apple, "[data-reset]")
                expect(apple.locator('[aria-current="step"]')).to_have_attribute("data-station", "left")
                self._no_network_side_effects(requests, errors)
            finally:
                browser.close()

    def test_portrait_touch_sets_and_picture_controls(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                _context, page, requests, errors = self._open(browser, mobile=True)
                page.locator(f'a[href="{PATH}#mengen"]').tap()
                sets = page.locator("#mengen")
                mode = sets.locator("[data-set-operation]")
                expect(mode).to_be_enabled()
                mode.select_option("or")
                sets.locator('[data-token="rc"]').tap()
                sets.locator('[data-token="yt"]').tap()
                sets.locator("[data-set-check]").tap()
                expect(sets.locator("[data-set-result]")).to_contain_text("4 matching tile(s) missing")
                expect(sets.locator('[data-token="rc"]')).to_have_attribute("data-result", "right")
                expect(sets.locator('[data-token="yt"]')).to_have_attribute("data-result", "outside")
                mode.select_option("difference")
                sets.locator('[data-token="rs"]').tap()
                sets.locator('[data-token="rt"]').tap()
                sets.locator("[data-set-check]").tap()
                expect(sets.locator("[data-set-result]")).to_contain_text("Exactly right")

                page.goto(self.origin + "/kids/", wait_until="domcontentloaded")
                page.locator(f'a[href="{PATH}#sicherheit"]').tap()
                story = page.locator("#sicherheit")
                back, next_picture, replay = (story.locator(f"[data-story-{key}]") for key in ("back", "next", "replay"))
                progress = story.locator("[data-story-progress]")
                expect(progress).to_have_text("Picture 1 of 4")
                plug = story.locator('[data-story-scene="outlet"] .story-plug')
                expect(plug).to_be_visible()
                expect(plug.locator("path")).to_have_css("stroke", "rgb(38, 49, 66)")
                expect(story.locator('[data-story-scene="outlet"] figcaption')).to_have_text(
                    "Leave the plug alone. Step back and get a grown-up."
                )
                expect(back).to_be_disabled()
                target = os.getenv("SIMPLEOFFICE_LOGIC_SCREENSHOTS")
                for n, key in enumerate(("outlet", "dropped", "siblings", "normal-switch"), start=1):
                    visible = story.locator(f'figure[data-story-scene="{key}"]')
                    expect(visible).to_be_visible()
                    expect(progress).to_have_text(f"Picture {n} of 4")
                    self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                    bounds = visible.bounding_box()
                    self.assertIsNotNone(bounds)
                    self.assertLessEqual(bounds["width"], 360)
                    if target:
                        destination = Path(target)
                        destination.mkdir(parents=True, exist_ok=True)
                        visible.screenshot(path=str(destination / f"{n}-{key}.png"))
                    if n < 4:
                        next_picture.tap()
                expect(next_picture).to_be_disabled()
                expect(replay).to_be_focused()
                back.tap()
                expect(progress).to_have_text("Picture 3 of 4")
                replay.tap()
                expect(progress).to_have_text("Picture 1 of 4")
                expect(next_picture).to_be_focused()
                self._no_network_side_effects(requests, errors)
            finally:
                browser.close()
