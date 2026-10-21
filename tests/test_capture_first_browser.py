"""Capture-first photo intake browser checks on desktop and mobile engines."""
from __future__ import annotations

import importlib.util
import io
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app import app
from app import db as database
from PIL import Image


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class CaptureFirstBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        credentials = {"username": "capture-browser-user", "password": "browser-capture-password"}
        self.client.post("/auth/register", data=credentials)
        self.client.post("/auth/login", data=credentials)
        cookie = self.client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        app.config.update(self.saved)
        self.temp.cleanup()

    @staticmethod
    def _jpeg_bytes(seed: int) -> bytes:
        output = io.BytesIO()
        Image.new("RGB", (12, 12), (seed, seed + 20, seed + 40)).save(output, format="JPEG")
        return output.getvalue()

    def test_camera_snapshot_and_multi_photo_import_work_on_desktop_android_and_ios(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            scenarios = (
                (playwright.chromium, {"viewport": {"width": 1280, "height": 900}}, "desktop"),
                (playwright.chromium, playwright.devices["Pixel 7"], "android"),
                (playwright.webkit, playwright.devices["iPhone 14"], "ios"),
            )
            for index, (browser_type, device_options, label) in enumerate(scenarios):
                with self.subTest(platform=label):
                    self._exercise_capture(browser_type, device_options, index)

    def _exercise_capture(self, browser_type, device_options, seed: int):
        browser = browser_type.launch()
        context = browser.new_context(**device_options)
        context.add_cookies([{
            "name": app.config.get("SESSION_COOKIE_NAME", "session"),
            "value": self.cookie,
            "url": self.base_url,
        }])
        try:
            page = context.new_page()
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(self.base_url + "/documents/images")
            snapshot_input = page.locator("#photo-snapshot-file")
            self.assertEqual("environment", snapshot_input.get_attribute("capture"))
            self.assertTrue(snapshot_input.is_visible())

            with page.expect_response(
                lambda response: response.url.endswith("/documents/photos/upload")
                and response.request.method == "POST"
            ) as uploaded:
                with page.expect_navigation(wait_until="domcontentloaded"):
                    snapshot_input.set_input_files({
                        "name": f"capture-{seed}.jpg", "mimeType": "image/jpeg",
                        "buffer": self._jpeg_bytes(80 + seed),
                    })
                    page.locator("#photo-snapshot-submit").click()
            self.assertEqual(200, uploaded.value.status)
            self.assertTrue(uploaded.value.json()["ok"])
            self.assertGreaterEqual(page.locator("img.card-img-top").count(), 1)

            bulk_input = page.locator("#photo-files")
            self.assertTrue(bulk_input.evaluate("node => node.multiple"))
            bulk_input.set_input_files([
                {"name": f"gallery-a-{seed}.jpg", "mimeType": "image/jpeg", "buffer": self._jpeg_bytes(110 + seed)},
                {"name": f"gallery-b-{seed}.jpg", "mimeType": "image/jpeg", "buffer": self._jpeg_bytes(150 + seed)},
            ])
            self.assertIn("2 Foto(s)", page.locator("#photo-selection").inner_text())
            page.locator("#photo-start").click()
            page.wait_for_function(
                "document.getElementById('photo-summary').textContent.startsWith('2 neu')",
                timeout=30000,
            )
            self.assertEqual(2, page.locator("#photo-results a").count())
            self.assertEqual([], page_errors)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
