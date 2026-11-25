"""Drive the signed-in production page with actual Chromium input semantics."""
from __future__ import annotations

import threading
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from tests.http_api_fixture import PublicHttpTestCase
from tests.test_kids_shinobi import GAME


@unittest.skipIf(sync_playwright is None, "Install the existing web-export test extra and Chromium")
class ShinobiBrowserTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        cookie = self.client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.cookie = {"name": cookie.key, "value": cookie.value, "url": self.origin}

    def _stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def _page(self, browser, touch):
        options = {"viewport": {"width": 345, "height": 750}, "is_mobile": True, "has_touch": True} if touch else {}
        context = browser.new_context(service_workers="block", **options)
        context.add_cookies([self.cookie])
        context.add_init_script("""(() => {
          window.missionMediaRequests = [];
          if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
            window.missionMediaRequests.push('camera');
            return Promise.reject(new DOMException('fixture has no camera', 'NotFoundError'));
          };
          window.Audio = function () { window.missionMediaRequests.push('audio'); throw Error('no audio device'); };
          if ('speechSynthesis' in window) speechSynthesis.speak = () => window.missionMediaRequests.push('speech');
        })();""")
        page = context.new_page()
        network, errors = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(self.origin + "/")
                   else (network.append(r.request.url), r.abort()))
        page.goto(self.origin + "/kids/", wait_until="domcontentloaded")
        card = page.locator(f'main a[href="{GAME}"]')
        (card.tap if touch else card.click)()
        expect(page).to_have_url(self.origin + GAME)
        expect(page.locator("[data-shinobi-start]")).to_be_enabled()
        return context, page, network, errors

    def test_keyboard_wrong_pick_replay_keeps_progress_and_back(self):
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            context, page, network, errors = self._page(browser, touch=False)
            try:
                start = page.locator("[data-shinobi-start]")
                start.focus()
                page.keyboard.press("Enter")
                lantern = page.locator('[data-shinobi-card="lantern"]')
                bridge = page.locator('[data-shinobi-card="bridge"]')
                expect(lantern).to_be_focused()
                page.keyboard.press("ArrowRight")
                expect(bridge).to_be_focused()
                page.keyboard.press("Enter")
                expect(bridge).to_have_attribute("data-wrong", "true")
                expect(page.locator('[data-slot="done"]')).to_have_count(0)
                page.keyboard.press("ArrowLeft")
                page.keyboard.press("Space")
                expect(page.locator('[data-slot="done"]')).to_have_count(1)
                look = page.locator("[data-shinobi-look]")
                look.focus()
                page.keyboard.press("Enter")
                expect(start).to_be_focused()
                expect(page.locator('[data-slot="done"]')).to_have_count(1)
                page.keyboard.press("Enter")
                page.keyboard.press("ArrowRight")
                expect(bridge).to_be_focused()
                page.keyboard.press("Enter")
                expect(page.locator("[data-shinobi-next]")).to_be_focused()
                expect(page.locator('[data-slot="done"]')).to_have_count(2)
                self.assertEqual(page.evaluate("missionMediaRequests"), [])
                page.locator('main a[href="/kids/"]').click()
                expect(page).to_have_url(self.origin + "/kids/")
                self.assertEqual(network, [])
                self.assertEqual(errors, [])
            finally:
                context.close()

    def test_touch_all_three_trails_repeat_reset_and_no_media(self):
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            context, page, network, errors = self._page(browser, touch=True)
            try:
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                start = page.locator("[data-shinobi-start]")
                step = lambda name: page.locator(f'[data-shinobi-card="{name}"]').tap()
                start.tap()
                step("lantern")
                expect(page.locator('[data-slot="done"]')).to_have_count(1)
                page.locator("[data-shinobi-retry]").tap()
                expect(page.locator('[data-slot="done"]')).to_have_count(0)
                for order in [("lantern", "bridge"), ("leaf", "bridge", "shell"), ("shell", "lantern", "leaf", "lantern")]:
                    if start.is_visible():
                        start.tap()
                    for name in order:
                        step(name)
                    expect(page.locator('[data-slot="done"]')).to_have_count(len(order))
                    page.locator("[data-shinobi-next]").tap()
                expect(page.locator("[data-shinobi-round-count]")).to_contain_text("1")
                expect(page.locator('[data-slot="done"]')).to_have_count(0)
                self.assertEqual(page.evaluate("missionMediaRequests"), [])
                self.assertEqual(network, [])
                self.assertEqual(errors, [])
            finally:
                context.close()
