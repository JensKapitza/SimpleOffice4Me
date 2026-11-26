"""Real Hand Landmarker/Chromium with #1307's pinned public test picture as video.

No images from a real device, model stubbing, asset downloads, or gaze claims.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

try:
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from http_api_fixture import PublicHttpTestCase

PROOF = "/documents/bedienhilfe"
TRACE = os.getenv("SIMPLEOFFICE_HAND_NAV_PRIVATE_TRACE") == "1"
HALF_RESOLUTION = os.getenv("SIMPLEOFFICE_HAND_NAV_PRIVATE_AB_SCALE") == "1"
TRACE_SCRIPT = Path(__file__).with_name("access_hand_browser_trace.js")


class HandStagingBrowserTests(PublicHttpTestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(os.getenv("SIMPLEOFFICE_HAND_NAV_FIXTURES", ""))
        cls.assets = Path(os.getenv("SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR", ""))
        if sync_playwright is None or not (cls.assets / "hand_landmarker.task").is_file() or not all(
            (cls.fixture_dir / name).is_file()
            for name in ("blank.y4m", "pointing.y4m", "hand-nav-release-then-index.y4m")
        ):
            message = "Provide Playwright and the explicit local MediaPipe/video fixtures"
            if os.getenv("SIMPLEOFFICE_HAND_NAV_BROWSER_REQUIRED") == "1":
                raise RuntimeError(message)
            raise unittest.SkipTest(message)

    def setUp(self):
        super().setUp()
        settings = patch.dict(app.config, {"HAND_NAV_STAGING": True, "KIDS_HAND_GAME_ASSET_DIR": str(self.assets)})
        settings.start()
        self.addCleanup(settings.stop)
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

    def _open(self, playwright, name):
        # Select full Chromium in new headless mode. The separate headless
        # shell inherits a media delegate that can reject capture before it
        # returns a track, even after a permission grant.
        browser = playwright.chromium.launch(channel="chromium", headless=True, args=[
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-video-capture={(self.fixture_dir / name).resolve()}",
            "--enable-unsafe-swiftshader",
            "--disable-dev-shm-usage",
        ])
        context = browser.new_context(service_workers="block")
        context.add_cookies([{**self.cookies, "url": self.origin}])
        context.grant_permissions(["camera"], origin=self.origin)
        # Only instrumentation: no replacement of the actual native stream or frames.
        context.add_init_script("""(() => {
            if (!navigator.mediaDevices) return;
            const native = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            window.handTest = [];
            navigator.mediaDevices.getUserMedia = async (constraints) => {
                window.handTest.push(constraints);
                try {
                    const input = await native(constraints);
                    window.handTestTracks = input.getTracks();
                    window.handTestMedia = 'granted';
                    return input;
                } catch (error) {
                    window.handTestMedia = error.name;
                    throw error;
                }
            };
        })()""")
        page = context.new_page()
        requests, outbound = [], []
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.origin + "/")
                   else (outbound.append(route.request.url), route.abort()))
        page.on("request", lambda req: requests.append((req.method, req.url)))
        page.goto(self.origin + "/", wait_until="domcontentloaded")
        page.locator(f'main a[href="{PROOF}"]').click()
        self.assertEqual(page.evaluate("window.handTest.length"), 0)
        self.assertFalse(any("/handzeichen/assets/" in url for _, url in requests))
        expect(page.locator("[data-hand-nav-start]")).to_be_enabled()
        expect(page.locator("[data-hand-nav-arm]")).to_be_disabled()
        page.evaluate("""() => {
          const status = document.querySelector('[data-hand-nav-status]');
          window.handTestStages = [status.dataset.state];
          new MutationObserver(() => window.handTestStages.push(status.dataset.state))
            .observe(status, {attributes: true, attributeFilter: ['data-state']});
        }""")
        return browser, page, requests, outbound

    def _start_native_preview(self, page):
        page.locator("[data-hand-nav-start]").click()
        # A failed start is a hard failure with stages, not a long wait for a
        # hidden element that loses the browser's exact startup evidence.
        status = page.locator("[data-hand-nav-status]")
        page.wait_for_function("""() => {
          const phase = document.querySelector('[data-hand-nav-status]').dataset.state;
          return ['live', 'model_error', 'camera_error', 'inference_error', 'denied'].includes(phase)
            || (phase === 'ended' && window.handTestStages.includes('loading'));
        }""", timeout=25000)
        snapshot = page.evaluate("""() => ({
          stages: [...new Set(window.handTestStages)],
          media: window.handTestMedia ?? 'not requested',
          tracks: window.handTestTracks?.map(t => t.kind + ':' + t.readyState) ?? [],
          visibility: document.visibilityState,
          focus: document.hasFocus(),
          previewMuted: document.querySelector('[data-hand-nav-video]').muted,
        })""")
        self.assertEqual("live", status.get_attribute("data-state"), snapshot)
        expect(page.locator("[data-hand-nav-video]")).to_be_visible()
        self.assertEqual(snapshot["tracks"], ["video:live"])
        self.assertTrue(page.evaluate("window.handTest.length === 1 && window.handTest[0].audio === false"))

    def _attach_fixture_trace(self, page):
        if not TRACE:
            return
        runtime = page.locator("[data-hand-nav]").get_attribute("data-runtime")
        self.assertEqual(runtime, "/kids/lernen/handzeichen/assets/vision_bundle.mjs")
        self.assertEqual([640, 480], page.evaluate("""() => {
            const video = document.querySelector('[data-hand-nav-video]');
            return [video.videoWidth, video.videoHeight];
        }"""))
        page.on("console", lambda message: print(message.text, flush=True)
                if message.text.startswith("HAND_NAV_FIXTURE_TRACE ") else None)
        page.evaluate(TRACE_SCRIPT.read_text(encoding="utf-8"), {
            "runtime": self.origin + runtime,
            "logic": self.origin + "/static/js/kids_hand_logic.mjs",
            "halfResolution": HALF_RESOLUTION,
        })

    def _emit_fixture_trace(self, page, case):
        if TRACE and page.url == self.origin + PROOF:
            try:
                trace = page.evaluate("() => window.handTestTrace?.() ?? null")
            except PlaywrightError as error:
                trace = {"unavailable": type(error).__name__}
            if trace is not None:
                print("HAND_NAV_SYNTHETIC_TRACE", json.dumps({"case": case, "trace": trace},
                                                            separators=(",", ":")), flush=True)

    @staticmethod
    def _observe_stop_after_start(page):
        page.evaluate("""() => {
          const [track] = window.handTestTracks;
          const nativeStop = track.stop.bind(track);
          track.stop = () => {
            nativeStop();
            sessionStorage.setItem('hand-nav-fixture-only-stop', track.readyState);
          };
        }""")

    def test_pinned_real_model_routes_after_public_index_and_deliberate_blank(self):
        with sync_playwright() as playwright:
            for key, path in (("dashboard", "/documents/dashboard"), ("documents", "/documents/")):
                with self.subTest(destination=key):
                    browser, page, requests, outbound = self._open(playwright, "hand-nav-release-then-index.y4m")
                    try:
                        page.locator("[data-hand-nav-destination]").select_option(key)
                        self._start_native_preview(page)
                        self._observe_stop_after_start(page)
                        self._attach_fixture_trace(page)
                        page.locator("[data-hand-nav-arm]").click()
                        expect(page.locator("[data-hand-nav-status]")).to_have_attribute("data-state", "armed", timeout=9000)
                        page.wait_for_url(self.origin + path, timeout=12000)
                        self.assertEqual(page.evaluate("sessionStorage.getItem('hand-nav-fixture-only-stop')"), "ended")
                        self.assertEqual(outbound, [])
                        self.assertTrue(all(method == "GET" for method, _ in requests))
                        self.assertTrue(any(url.endswith("hand_landmarker.task") for _, url in requests))
                        self.assertTrue(any(url.endswith("vision_wasm_nosimd_internal.wasm") for _, url in requests))
                    finally:
                        try:
                            self._emit_fixture_trace(page, "combined-" + key)
                        finally:
                            browser.close()

    def test_real_blank_and_held_index_cannot_open_anything_then_stop(self):
        with sync_playwright() as playwright:
            for name, state in (("blank.y4m", "armed"), ("pointing.y4m", "release")):
                with self.subTest(video=name):
                    browser, page, _, outbound = self._open(playwright, name)
                    try:
                        self._start_native_preview(page)
                        self._attach_fixture_trace(page)
                        page.locator("[data-hand-nav-arm]").click()
                        # An actual hold window elapses. The page must not leave.
                        page.wait_for_timeout(2200)
                        expect(page.locator("[data-hand-nav-status]")).to_have_attribute("data-state", state)
                        self.assertEqual(page.url, self.origin + PROOF)
                        page.keyboard.press("Escape")
                        expect(page.locator("[data-hand-nav-status]")).to_have_attribute("data-state", "ended")
                        self.assertTrue(page.locator("[data-hand-nav-video]").evaluate("v => v.srcObject === null"))
                        self.assertEqual(page.evaluate("window.handTestTracks.map(t => t.readyState)"), ["ended"])
                        self.assertEqual(outbound, [])
                    finally:
                        try:
                            self._emit_fixture_trace(page, name)
                        finally:
                            browser.close()
