"""Authenticated Chromium check for a USB reader's keyboard-mode input."""
from __future__ import annotations

import os
import re
import threading
import unittest
from urllib.parse import urlsplit

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
                with page.expect_response(lambda reply: urlsplit(reply.url).path == "/inventory/nfc-find") as call:
                    page.keyboard.press("Enter")
                reply = call.value
                self.assertEqual("POST", reply.request.method)
                self.assertEqual("", urlsplit(reply.url).query)
                self.assertEqual({"uid": "04AA12FF"}, reply.request.post_data_json)
                self.assertEqual("Previously stored tag", reply.json()["matches"][0]["name"])
                self.assertEqual("private, no-store", reply.headers["cache-control"])
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

                responses = []
                page.on("request", lambda request: responses.append(request)
                        if urlsplit(request.url).path == "/inventory/nfc-find" else None)
                page.evaluate("""() => {
                    window.nfcStatusFixture = 'disabled';
                    window.SimpleOfficeNativeNfc.start = id => {
                        window.currentNfcRequest = id;
                        const state = window.nfcStatusFixture;
                        if (state === 'waiting') return 'ok';
                        if (state === 'timeout' || state === 'no-id') {
                            setTimeout(() => window.dispatchEvent(new CustomEvent('simpleoffice:nfc', {
                                detail: {requestId: id, state}
                            })), 0);
                            return 'ok';
                        }
                        return state;
                    };
                }""")
                status = page.locator("[data-nfc-find-status]")
                for state, wording in (
                    ("disabled", r"Enable NFC|NFC in den Android-Einstellungen"),
                    ("unavailable", r"No direct NFC reader|Kein NFC-Direktleser"),
                    ("denied", r"permission was denied|Freigabe wurde .*verweigert"),
                    ("no-id", r"did not provide an NFC ID|keine NFC-ID geliefert"),
                    ("timeout", r"No tag read|Kein Tag gelesen"),
                ):
                    with self.subTest(state=state):
                        page.evaluate("value => window.nfcStatusFixture = value", state)
                        button.click()
                        expect(status).to_have_text(re.compile(wording))
                        expect(finder).to_have_value("04AA12FF")
                        self.assertIsNone(button.get_attribute("aria-busy"))
                        if state == "unavailable":
                            expect(finder).to_be_focused()
                page.evaluate("window.nfcStatusFixture = 'waiting'")
                button.click()
                expect(button).to_have_attribute("aria-busy", "true")
                button.click()
                expect(status).to_have_text(re.compile(r"scan cancelled|Scan abgebrochen"))
                page.evaluate("""() => window.dispatchEvent(new CustomEvent('simpleoffice:nfc', {
                    detail: {requestId: window.currentNfcRequest, state:'ok', serial:'ABCD'}
                }))""")
                expect(finder).to_have_value("04AA12FF")
                self.assertEqual([], responses)
                expect(page.locator("#nfc-id")).to_have_value("04AA12FF")
            finally:
                browser.close()
