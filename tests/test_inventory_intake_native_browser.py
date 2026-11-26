"""Signed-in mixed intake in Chromium with a fake Android MessagePort; no camera."""
from __future__ import annotations

import os
import threading
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from tests.http_api_fixture import PublicHttpTestCase


PATH = "/inventory/capture/intake"
BOOK = "9780140328721"
UPC = "036000291452"
INSTALL_FIXTURE = """
() => {
    const fixture = new MessageChannel();
    window.nativeTestPort = fixture.port1;
    window.nativeTestRequests = [];
    fixture.port1.onmessage = ({data}) => {
        if (data.startsWith("scan:")) window.nativeTestRequests.push(data);
    };
    window.postMessage("simpleoffice:intake-barcode/v1", location.origin, [fixture.port2]);
}
"""


class InventoryIntakeNativeBrowserTests(PublicHttpTestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED") != "1":
            raise unittest.SkipTest("Run with SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED=1")
        if sync_playwright is None:
            raise RuntimeError("Playwright and Chromium are required.")

    def setUp(self):
        super().setUp()
        response = self.client.post("/documents/settings/language", data={"language": "de"},
                                    headers=self._csrf_headers(self.client, PATH))
        self.assertEqual(302, response.status_code)
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def _stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    @staticmethod
    def _start(page, count):
        page.locator("[data-intake-native-scan]").tap()
        page.wait_for_function("count => window.nativeTestRequests.length === count", arg=count)
        return page.evaluate("window.nativeTestRequests.at(-1).slice(5)")

    @staticmethod
    def _reply(page, scan_id, state, code=""):
        page.evaluate("msg => window.nativeTestPort.postMessage(msg)", f"{scan_id}:{state}:{code}")

    def test_native_scan_persists_private_cards_and_retries_without_duplicates(self):
        with sync_playwright() as playwright:
            configured = os.getenv("SIMPLEOFFICE_INTAKE_CHROMIUM")
            options = {"executable_path": configured} if configured else {"channel": "chromium"}
            browser = playwright.chromium.launch(chromium_sandbox=True, **options)
            context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
                                          is_mobile=True, service_workers="block")
            session = self.client.get_cookie(app.config["SESSION_COOKIE_NAME"])
            context.add_cookies([{"name": session.key, "value": session.value, "url": self.origin}])
            page = context.new_page()
            unexpected, mutations, errors = [], [], []
            lost_ack, stalled = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route_request(route):
                req = route.request
                if not req.url.startswith(self.origin + "/"):
                    unexpected.append(req.url)
                    route.abort()
                    return
                if req.method != "GET":
                    mutations.append((req.method, req.url))
                    if req.url == self.origin + PATH:
                        if len(mutations) == 1:
                            lost_ack.append(route.fetch().status)
                            stalled.append(route)  # Do not settle the browser response yet.
                            return
                        if len(mutations) == 3:
                            route.abort()  # The second deliberate scan never reaches Flask.
                            return
                route.continue_()

            page.route("**/*", route_request)
            page.goto(self.origin + PATH, wait_until="domcontentloaded")
            form = page.locator("#intake-capture")
            barcode = form.locator("#intake-barcode")
            button = form.get_by_role("button", name="Barcode scannen")
            status = form.locator("[data-intake-native-scan-status]")
            expect(form).to_be_visible()
            expect(page.locator("[data-intake-native-scan]")).to_be_hidden()
            page.evaluate(INSTALL_FIXTURE)
            expect(button).to_be_visible()
            expect(form.locator("[data-intake-native-scan-help]")).to_contain_text("ein Foto wählen")
            page.evaluate("document.querySelector('[data-intake-native-scan]').click()")
            self.assertEqual([], page.evaluate("window.nativeTestRequests"))

            stale = self._start(page, 1)
            barcode.fill(UPC)
            barcode.fill("")  # Returning to the starting text is still an edit.
            self._reply(page, stale, "ok", BOOK)
            expect(status).to_contain_text("Eingabe geändert")
            expect(barcode).to_have_value("")

            scan_id = self._start(page, 2)
            self._reply(page, stale, "ok", BOOK)
            expect(button).to_be_disabled()
            self._reply(page, scan_id, "ok", "9780140328722")
            expect(status).to_contain_text("Kein unterstützter Barcode")
            expect(barcode).to_have_value("")

            scan_id = self._start(page, 3)
            self._reply(page, scan_id, "cancelled")
            expect(status).to_contain_text("abgebrochen")
            expect(status).to_contain_text("ein Foto wählen")
            expect(barcode).to_have_value("")

            scan_id = self._start(page, 4)
            self._reply(page, scan_id, "unavailable")
            expect(status).to_contain_text("Android-Scanner nicht verfügbar")
            expect(status).to_contain_text("ein Foto wählen")
            expect(barcode).to_have_value("")

            scan_id = self._start(page, 5)
            page.evaluate("location.hash = 'erfasste-entwuerfe'")
            self._reply(page, scan_id, "ok", BOOK)
            expect(button).to_be_enabled()
            expect(barcode).to_have_value("")

            scan_id = self._start(page, 6)
            page.evaluate("const form = document.querySelector('#intake-capture'); form.replaceWith(form.cloneNode(true))")
            self._reply(page, scan_id, "ok", BOOK)
            expect(barcode).to_have_value("")
            self.assertEqual([], mutations)

            page.reload(wait_until="domcontentloaded")
            expect(page.locator("[data-intake-native-scan]")).to_be_hidden()
            page.evaluate(INSTALL_FIXTURE)
            next_scan = self._start(page, 1)
            self.assertNotEqual(scan_id, next_scan)
            self._reply(page, scan_id, "ok", BOOK)
            expect(button).to_be_disabled()
            self._reply(page, next_scan, "ok", UPC)
            pending = page.locator("#intake-pending")
            expect(pending.locator("[data-intake-pending-barcode] code")).to_have_text(UPC)
            expect(pending.get_by_role("status")).to_contain_text("Speicherung nicht bestätigt", timeout=12000)
            self.assertEqual(1, len(stalled))
            retry = form.get_by_role("button", name="Scan erneut speichern")
            expect(retry).to_be_enabled()
            expect(button).to_be_disabled()
            expect(barcode).not_to_be_editable()
            expect(form.locator("#intake-photo")).to_be_disabled()
            self.assertEqual([201], lost_ack)
            expect(page.locator("article[id^=draft-]")).to_have_count(0)

            # The first POST reached the store. This sends the same capture token,
            # loading the existing draft instead of creating a copy.
            # The hash changes before the full reload. Wait for the new document's
            # deferred scripts before announcing its (document-scoped) port.
            with page.expect_event("domcontentloaded"):
                retry.tap()
            drafts = page.locator("article[id^=draft-]")
            expect(drafts).to_have_count(1)
            first_id = drafts.first.get_attribute("id")
            expect(drafts.first.locator("input[name=barcode]")).to_have_value(UPC)
            expect(page.locator("[data-intake-native-scan]")).to_be_hidden()
            page.evaluate(INSTALL_FIXTURE)
            expect(button).to_be_visible()
            self._reply(page, self._start(page, 1), "ok", UPC)
            expect(pending.get_by_role("status")).to_contain_text("Speicherung nicht bestätigt")
            expect(drafts).to_have_count(1)
            expect(retry).to_be_enabled()
            with page.expect_event("domcontentloaded"):
                retry.tap()
            expect(drafts).to_have_count(2)
            self.assertIn(first_id, [card.get_attribute("id") for card in drafts.all()])
            for card in drafts.all():
                expect(card).to_contain_text("In Prüfung")
                expect(card.locator("input[name=barcode]")).to_have_value(UPC)
            self.assertEqual(mutations, [("POST", self.origin + PATH)] * 4)
            self.assertEqual([], unexpected)
            self.assertEqual([], errors)
            context.close()
            browser.close()
