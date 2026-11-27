"""Authenticated Chromium check for a USB reader's keyboard-mode input."""
from __future__ import annotations

import os
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from app.object_store import ObjectStore
from tests.http_api_fixture import PublicHttpTestCase


class InventoryNfcBrowserTests(PublicHttpTestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED") != "1":
            raise unittest.SkipTest("Set SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED=1 for Chromium")
        if sync_playwright is None:
            raise RuntimeError("Install the project's [test,web-export] extras and Chromium")

    def test_usb_uid_is_reviewable_and_never_submits_on_enter(self):
        objects = ObjectStore(app.config["DOCUMENT_ROOT"])
        objects.create({
            "name": "Previously stored tag", "type": "object",
            "fields": "nfc_id=04:aa:12:ff | https://example.invalid/metadata",
        }, "owner")
        server = make_server("127.0.0.1", 0, app, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        cookie_name = app.config["SESSION_COOKIE_NAME"]
        cookie = self.client.get_cookie(cookie_name)

        with sync_playwright() as playwright:
            executable = os.getenv("SIMPLEOFFICE_INTAKE_CHROMIUM")
            # CI installs the full Chromium channel with --no-shell, not headless_shell.
            options = {"executable_path": executable} if executable else {"channel": "chromium"}
            browser = playwright.chromium.launch(headless=True, **options)
            try:
                ctx = browser.new_context()
                ctx.add_cookies([{"name": cookie_name, "value": cookie.value, "url": origin}])
                page = ctx.new_page()
                page.goto(origin + "/inventory", wait_until="domcontentloaded")
                page.locator("#title").fill("New object if submitted")
                page.locator("#nfc-keyboard").click()
                expect(page.locator("#nfc-id")).to_be_focused()
                page.keyboard.type("04-aa-12-ff")
                with page.expect_response(lambda reply: urlsplit(reply.url).path == "/inventory/find") as call:
                    page.keyboard.press("Enter")
                reply = call.value
                self.assertEqual(["04AA12FF"], parse_qs(urlsplit(reply.url).query)["nfc"])
                self.assertTrue(reply.json()["found"])
                expect(page.locator("#nfc-id")).to_have_value("04AA12FF")
                expect(page.locator("#scan-status")).to_contain_text("Previously stored tag")

                page.locator("#nfc-keyboard").click()
                page.keyboard.type("https://example.invalid/payload")
                page.keyboard.press("Enter")
                expect(page.locator("#nfc-id")).to_have_value("04AA12FF")
                self.assertEqual(origin + "/inventory", page.url)
                self.assertEqual(1, len(objects.objects()))
                objects.create({"name": "Same tag on second item", "type": "object",
                                "fields": "nfc_id=04AA12FF"}, "owner")
                finder = page.locator("#nfc-find-uid")
                finder.fill("04-aa-12-ff")
                with page.expect_response(lambda reply: urlsplit(reply.url).path == "/inventory/nfc-find") as search:
                    finder.press("Enter")
                self.assertEqual("POST", search.value.request.method)
                self.assertEqual("", urlsplit(search.value.url).query)
                self.assertEqual(200, search.value.status)
                self.assertEqual("private, no-store", search.value.headers["cache-control"])
                expect(page.locator("[data-nfc-find-results] a")).to_have_count(2)
                expect(page.locator("[data-nfc-find-results]")).to_contain_text("Previously stored tag")
                expect(page.locator("[data-nfc-find-results]")).to_contain_text("Same tag on second item")
                expect(page.locator("#title")).to_have_value("New object if submitted")
                expect(page.locator("#nfc-id")).to_have_value("04AA12FF")
                self.assertEqual(origin + "/inventory", page.url)
                self.assertEqual(2, len(objects.objects()))

                page.evaluate("""() => {
                    window.dispatchEvent(new Event('simpleoffice:native-ready'));
                    window.SimpleOfficeNativeNfc = {
                        start(requestId) {
                            setTimeout(() => window.dispatchEvent(new CustomEvent('simpleoffice:nfc', {
                                detail: {requestId, state:'ok', serial:'04:AA:12:FF'}
                            })), 10);
                            return 'ok';
                        },
                        stop() {},
                    };
                }""")
                button = page.locator("[data-nfc-find-scan]")
                expect(button).to_be_visible()
                with page.expect_response(lambda reply: urlsplit(reply.url).path == "/inventory/nfc-find") as native:
                    button.click()
                self.assertEqual(200, native.value.status)
                expect(page.locator("[data-nfc-find-results] a")).to_have_count(2)
                self.assertEqual(origin + "/inventory", page.url)
            finally:
                browser.close()
