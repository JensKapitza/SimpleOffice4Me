"""Exercise the WebView-facing motion UI in real Chromium with a synthetic native bridge."""
import importlib.util
import os
import threading
import unittest

from werkzeug.serving import make_server

from app import app
from app.datalogger_store import DataLoggerStore
from tests.http_api_fixture import PublicHttpTestCase


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional browser test dependency")
class AndroidMotionBrowserTest(PublicHttpTestCase):
    def test_preview_does_not_store_before_trusted_save(self):
        from playwright.sync_api import Error, sync_playwright

        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        motion = store.create_channel("Bewegung", "owner", unit="m/s²")
        turn = store.create_channel("Drehung", "owner", unit="rad/s")
        cookie_name = app.config["SESSION_COOKIE_NAME"]
        cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(cookie)
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(executable_path=os.environ.get("SIMPLEOFFICE_TEST_CHROMIUM") or None)
                except Error as exc:
                    if "Executable doesn't exist" in str(exc):
                        self.skipTest("install Playwright Chromium")
                    raise
                try:
                    context = browser.new_context(service_workers="block")
                    context.add_cookies([{"name": cookie_name, "value": cookie.value, "url": origin}])
                    page = context.new_page()
                    self._exercise(page, origin, store, motion, turn)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(5)

    def _exercise(self, page, origin, store, motion, turn):
        page.add_init_script("""
            window.motionRequests = [];
            window.SimpleOfficeAndroid = {requestMotion(token,nonce,kind) {
                window.motionRequests.push(kind);
                window.SimpleOfficeAndroidMotion.result('stale',kind,'captured',99);
                window.SimpleOfficeAndroidMotion.result(nonce,kind,'captured',13);
                return 'pending';
            }};
        """)
        page.goto(origin + "/datalogger/android-motion")
        measure = page.locator('[data-motion-kind="acceleration"]')
        self.assertTrue(measure.is_disabled())
        page.evaluate("SimpleOfficeAndroidMotion.bind('fixture', 'active', true, false)")
        self.assertTrue(page.locator('[data-motion-kind="angular_speed"]').is_disabled())
        measure.evaluate("(b) => b.click()")  # synthetic DOM event, not a user gesture
        self.assertEqual([], page.evaluate("motionRequests"))
        measure.click()
        self.assertIn("13 m/s²", page.locator("[data-motion-status]").inner_text())
        self.assertEqual([], store.samples(motion))
        self.assertTrue(page.locator(f'option[value="{turn}"]').is_disabled())
        page.locator("#motion-channel").select_option(motion)
        page.evaluate("SimpleOfficeAndroidMotion.clear()")
        self.assertTrue(page.locator("[data-motion-save] button").is_disabled())
        self.assertEqual([], store.samples(motion))
        page.evaluate("SimpleOfficeAndroidMotion.bind('fixture', 'new-session', true, false)")
        measure.click()
        page.locator("#motion-channel").select_option(motion)
        page.locator("[data-motion-save] button").click()
        page.wait_for_url(f"**/datalogger/channels/{motion}")
        self.assertEqual([13], [s["value"] for s in store.samples(motion)])
