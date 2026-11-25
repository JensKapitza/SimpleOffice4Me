"""Optional real Chromium/MediaPipe with an upstream public image as fake webcam video.

No physical webcam, recordings or private images are accessed. This test never
downloads assets. See docs/KIDS_HAND_GAME.md for the explicit fixture setup.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from http_api_fixture import PublicHttpTestCase


class KidsHandGameBrowserTests(PublicHttpTestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("Playwright and Chromium are required")
        cls.fixture = os.environ.get("SIMPLEOFFICE_KIDS_HAND_PUBLIC_TEST_VIDEO", "")
        cls.blank_fixture = os.environ.get("SIMPLEOFFICE_KIDS_HAND_BLANK_TEST_VIDEO", "")
        cls.assets = os.environ.get("SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR", "")
        if not cls.fixture or not Path(cls.fixture).is_file() or not cls.assets:
            raise unittest.SkipTest("Provide locally verified MediaPipe files and the upstream public webcam fixture")

    def setUp(self):
        super().setUp()
        cfg = patch.dict(app.config, {"KIDS_HAND_GAME_ASSET_DIR": self.assets})
        cfg.start()
        self.addCleanup(cfg.stop)
        language = self.client.post("/documents/settings/language", data={"language": "en"},
                                    headers=self._csrf_headers(self.client))
        self.assertEqual(302, language.status_code)
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

    def _browser(self, pw, fixture):
        browser = pw.chromium.launch(executable_path=shutil.which("chromium"), headless=True, args=[
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-video-capture={Path(fixture).resolve()}",
            "--enable-unsafe-swiftshader",
            "--disable-dev-shm-usage",
        ])
        context = browser.new_context(service_workers="block")
        context.add_cookies([{**self.cookies, "url": self.origin}])
        context.grant_permissions(["camera"], origin=self.origin)
        return browser, context

    def test_real_public_hand_frame_from_signed_in_kids_navigation(self):
        with sync_playwright() as pw:
            browser, context = self._browser(pw, self.fixture)
            # Observe original browser media promises without ever substituting a
            # frame nor exposing it in logs. First click returns a scripted deny;
            # second uses genuine browser media permission with a fake device.
            context.add_init_script("""(() => {
              if (!navigator.mediaDevices) return;
              const media = navigator.mediaDevices;
              const original = media.getUserMedia.bind(media);
              window.handFixtureCalls = [];
              media.getUserMedia = async (constraints) => {
                window.handFixtureCalls.push(constraints);
                if (window.handFixtureCalls.length === 1) throw new DOMException('test denial', 'NotAllowedError');
                const stream = await original(constraints);
                window.handFixtureTracks = stream.getTracks();
                return stream;
              };
            })();""")
            page = context.new_page()
            outbound = []
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.origin + "/")
                       else (outbound.append(route.request.url), route.abort()))
            seen = []
            page.on("request", lambda r: seen.append((r.method, r.url)))
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            try:
                page.goto(self.origin + "/kids/", wait_until="domcontentloaded")
                page.locator('main a[href="/kids/lernen/handzeichen"]').click()
                start = page.locator("[data-hand-camera]")
                expect(start).to_be_enabled()
                self.assertEqual(page.evaluate("window.handFixtureCalls.length"), 0)
                self.assertFalse(any("/handzeichen/assets/" in url for _, url in seen))
                # Crystal -> Star -> Shield -> Spark; choose the public fixture's shape.
                for _ in range(3):
                    page.locator("[data-hand-next]").click()
                start.click()
                expect(page.locator("[data-hand-camera-status]")).to_contain_text("denied", timeout=20000)
                self.assertEqual(page.evaluate("window.handFixtureCalls.length"), 1)
                start.click()
                expect(page.locator("[data-hand-preview]")).to_be_visible(timeout=20000)
                expect(page.locator("[data-hand-feedback]")).to_contain_text("Great!", timeout=25000)
                self.assertTrue(page.evaluate("window.handFixtureCalls.every(c => c.audio === false)"))
                self.assertEqual(page.evaluate("window.handFixtureTracks.map(t => t.kind)"), ["video"])
                self.assertTrue(page.locator("[data-hand-overlay]").evaluate(
                    "c => c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((x,i) => i%4===3 && x>0)"
                ))
                self.assertEqual(page.locator("[data-hand-preview]").get_attribute("data-mirrored"), "true")
                page.locator("[data-hand-mirror]").uncheck()
                self.assertEqual(page.locator("[data-hand-preview]").get_attribute("data-mirrored"), "false")
                page.locator("[data-hand-duel]").click()
                page.locator("[data-hand-round-start]").click()
                expect(page.locator('[data-hand-steps] li[data-complete="true"]')).to_have_count(1, timeout=10000)
                expect(page.locator("[data-hand-title]")).to_have_text("Star")
                page.locator("[data-hand-stop]").click()
                self.assertEqual(page.evaluate("window.handFixtureTracks.map(t => t.readyState)"), ["ended"])
                start.click()
                expect(page.locator("[data-hand-preview]")).to_be_visible(timeout=20000)
                # Test-only, same-tab observer. The browser automatically ending
                # the device on navigation does not call our JS wrapper, so a
                # passing assertion here means the application stopped the track.
                page.evaluate("""() => {
                  sessionStorage.removeItem('kids-hand-test-nav-stop');
                  const [track] = window.handFixtureTracks;
                  const originalStop = track.stop.bind(track);
                  track.stop = () => {
                    originalStop();
                    sessionStorage.setItem('kids-hand-test-nav-stop', track.readyState);
                  };
                }""")
                page.locator('[data-kids-header] a[href="/kids/"]').click()
                page.wait_for_url(self.origin + "/kids/")
                self.assertEqual(page.evaluate("sessionStorage.getItem('kids-hand-test-nav-stop')"), "ended")
                self.assertEqual(outbound, [])
                self.assertFalse(any(method != "GET" for method, _ in seen))
                self.assertTrue(any(url.endswith("vision_wasm_nosimd_internal.wasm") for _, url in seen))
                self.assertTrue(any(url.endswith("hand_landmarker.task") for _, url in seen))
                self.assertEqual([], [e for e in errors if not e.startswith("INFO:")])
            finally:
                browser.close()

    def test_real_model_blank_frame_never_scores(self):
        if not self.blank_fixture or not Path(self.blank_fixture).is_file():
            self.skipTest("Provide the purely generated blank webcam fixture")
        with sync_playwright() as pw:
            browser, context = self._browser(pw, self.blank_fixture)
            page = context.new_page()
            page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(self.origin + "/") else r.abort())
            try:
                page.goto(self.origin + "/kids/", wait_until="domcontentloaded")
                page.locator('main a[href="/kids/lernen/handzeichen"]').click()
                page.locator("[data-hand-camera]").click()
                expect(page.locator("[data-hand-preview]")).to_be_visible(timeout=20000)
                expect(page.locator("[data-hand-signal]")).to_have_text("Show one hand.")
                page.locator("[data-hand-duel]").click()
                page.locator("[data-hand-round-start]").click()
                expect(page.locator("[data-hand-clock]")).to_have_text("29 seconds remaining", timeout=5000)
                expect(page.locator('[data-hand-steps] li[data-complete="true"]')).to_have_count(0)
                expect(page.locator("[data-hand-title]")).to_have_text("Spark")
                page.locator("[data-hand-stop]").click()
                expect(page.locator("[data-hand-preview]")).to_be_hidden()
                self.assertTrue(page.locator("[data-hand-video]").evaluate("v => v.srcObject === null"))
            finally:
                browser.close()
