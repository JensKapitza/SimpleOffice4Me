"""Mobile Chromium integration coverage for acoustic quick sharing."""
from __future__ import annotations

import importlib.util
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import app
from app import db as database


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright browser extra is required")
class AcousticShareBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION", "ALLOW_PUBLIC_REGISTRATION")
        }
        self.addCleanup(app.config.update, self.saved)
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            ALLOW_PUBLIC_REGISTRATION="1",
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        client = app.test_client()
        response = client.post(
            "/auth/register",
            data={"username": "acoustic-mobile", "password": "acoustic-mobile-password"},
        )
        self.assertEqual(302, response.status_code)
        response = client.post(
            "/auth/login",
            data={"username": "acoustic-mobile", "password": "acoustic-mobile-password"},
        )
        self.assertEqual(302, response.status_code)
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)

    def test_mobile_send_and_receive_generated_acoustic_signal(self):
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import expect

        executable = shutil.which("chromium")
        if not executable:
            self.skipTest("System Chromium is required")
        payload = "Mobile send: Grüße 🌍"
        incoming = "Rx café ☕"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable,
                headless=True,
                args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
            )
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
                response = page.goto(self.base_url + "/quick-share", wait_until="networkidle")
                self.assertEqual(200, response.status)
                container = page.locator("[data-acoustic-share]")
                container.locator("summary").tap()
                self.assertTrue(container.is_visible())
                text_input = page.locator("[data-acoustic-text]")
                result = page.locator("[data-acoustic-result]")
                status = page.locator("[data-acoustic-status]")
                band = page.locator("[data-acoustic-band]")
                fec = page.locator("[data-acoustic-fec]")
                send = page.locator("[data-acoustic-send]")
                receive = page.locator("[data-acoustic-receive]")

                self.assertTrue(text_input.is_visible())
                self.assertTrue(send.is_visible())
                self.assertTrue(receive.is_visible())
                self.assertEqual("high", band.input_value())
                self.assertEqual("hamming", fec.input_value())
                text_input.fill(payload)
                fec.select_option("repeat3")
                send.tap()
                expect(status).to_contain_text(
                    re.compile(r"complete|abgeschlossen", re.I),
                    timeout=15000,
                )

                # Feed a genuine modem waveform into the real receiver through a
                # Web Audio MediaStreamDestination, replacing only getUserMedia.
                # The page's decoder, framing, FEC, and output UI remain real.
                page.evaluate("""incoming => {
                  const sampleRate = 48000;
                  const context = new AudioContext({sampleRate});
                  const pcm = window.AcousticCodec.encode(incoming, {sampleRate, band: 'ultrasound', fec: 'repeat3'});
                  const leadSilence = 24000;
                  const buffer = context.createBuffer(1, leadSilence + pcm.length + 4096, sampleRate);
                  const channel = buffer.getChannelData(0);
                  channel.set(pcm, leadSilence);
                  const destination = context.createMediaStreamDestination();
                  const source = context.createBufferSource();
                  source.buffer = buffer;
                  source.connect(destination);
                  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
                    configurable: true,
                    value: async constraints => {
                      window.__acousticGetUserMediaConstraints = constraints;
                      await context.resume();
                      source.start();
                      return destination.stream;
                    }
                  });
                  window.__acousticTestAudio = {context, source, destination};
                }""", incoming)
                receive.tap()
                expect(result).to_have_value(incoming, timeout=15000)
                self.assertEqual(incoming, result.input_value())
                self.assertEqual("audio", page.evaluate("window.__acousticGetUserMediaConstraints.audio ? 'audio' : ''"))
                self.assertLessEqual(
                    page.locator("[data-acoustic-share]").evaluate("el => el.scrollWidth"),
                    page.locator("[data-acoustic-share]").evaluate("el => el.clientWidth") + 1,
                    "Acoustic share controls should fit the mobile viewport",
                )
                self.assertTrue(page.locator("[data-acoustic-stop]").is_disabled())
                context.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
