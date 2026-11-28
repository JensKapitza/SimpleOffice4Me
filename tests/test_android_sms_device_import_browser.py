"""Use the real archive view in Chromium with a stand-in for Android's port."""
import importlib.util
import os
from threading import Thread
import unittest

from werkzeug.serving import make_server

from app import app
from tests.http_api_fixture import PublicHttpTestCase


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class AndroidDeviceSmsBrowserTests(PublicHttpTestCase):
    def test_previews_are_reviewed_and_invalidated_in_the_real_archive_view(self):
        from playwright.sync_api import Error, expect, sync_playwright

        app.config.update(ANDROID_PRIVATE_SMS_READER=lambda *_: ("denied", []),
                          ANDROID_PRIVATE_SMS_LEASE_ACTIVE=lambda _: False)
        name = app.config["SESSION_COOKIE_NAME"]
        cookie = self.client.get_cookie(name)

        def localhost_apk_origin(environ, start_response):
            environ["HTTP_HOST"] = "127.0.0.1:8765"
            return app(environ, start_response)

        with make_server("127.0.0.1", 0, localhost_apk_origin, threaded=True) as server, sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(executable_path=os.environ.get("SIMPLEOFFICE_SMS_CHROMIUM") or None)
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    self.skipTest("install Playwright Chromium")
                raise
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"
                context = browser.new_context(service_workers="block", user_agent="SimpleOffice4Me-Android/fixture")
                context.add_cookies([{"name": name, "value": cookie.value, "url": origin}])
                page = context.new_page()
                page.goto(origin + "/communication/sms")
                section = page.locator("#sms-device-import")
                expect(section).to_be_hidden()
                page.evaluate("""() => {
                    const channel = new MessageChannel();
                    channel.port1.onmessage = event => {
                        const request = JSON.parse(event.data);
                        window.validDateRequest = request.from === '2025-06-15' && request.through === ''
                            && typeof request.csrf === 'string' && request.csrf.length >= 32
                            && Object.keys(request).length === 5;
                        window.stages = [...(window.stages || []), request.stage];
                        const ticket = 'a'.repeat(32);
                        const answer = request.stage === 'preview'
                            ? (request.through
                                ? {state: 'denied'}
                                : {state: 'preview', preview: ticket, added: 2, already: 1})
                            : {state: request.preview === ticket ? 'preview_changed' : 'unauthorized'};
                        channel.port1.postMessage(JSON.stringify(answer));
                    };
                    window.postMessage('simpleoffice:sms-device-import', location.origin, [channel.port2]);
                }""")
                expect(section).to_be_visible()
                section.locator("#sms-device-from").fill("2025-06-15")
                button = section.locator("#sms-device-preview")
                commit = section.locator("#sms-device-commit")
                expect(commit).to_be_hidden()
                button.click()
                status = section.locator("#sms-device-status")
                expect(status).to_contain_text("2 new messages, 1 already archived. Nothing saved yet.")
                expect(commit).to_be_visible()
                self.assertEqual(["preview"], page.evaluate("window.stages"))
                self.assertTrue(page.evaluate("window.validDateRequest"))
                commit.click()
                expect(status).to_contain_text("changed since the preview")
                expect(commit).to_be_hidden()
                self.assertEqual(["preview", "commit"], page.evaluate("window.stages"))
                button.click()
                expect(commit).to_be_visible()
                section.locator("#sms-device-through").fill("2025-06-15")
                expect(commit).to_be_hidden()
                button.click()
                expect(status).to_contain_text("did not grant")
                expect(button).to_be_enabled()
                self.assertEqual(origin + "/communication/sms", page.url)
            finally:
                browser.close()
                server.shutdown()
                thread.join(timeout=5)
