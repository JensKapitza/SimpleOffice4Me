"""Real browser acceptance for sharing the freshly downloaded PDF in-place."""

import os
import threading
import unittest

from werkzeug.serving import make_server

from app import app
from app.federation_store import FederationStore
from app.library.book_scan_shares import Shares
from tests import test_inventory_intake_browser as browser_fixture
from tests.http_api_fixture import PublicHttpTestCase
from tools.smoke_book_scans import image_file


class BookScannerBrowserTests(PublicHttpTestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED") != "1":
            raise unittest.SkipTest("Set SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED=1 for the Chromium gate")
        if browser_fixture.sync_playwright is None:
            raise RuntimeError("Use the project's [test,web-export] extras and Playwright Chromium.")

    def test_download_then_share_the_exact_pdf_without_reselecting_a_file(self):
        from playwright.sync_api import expect, sync_playwright

        root = app.config["DOCUMENT_ROOT"]
        federation = FederationStore(root)
        with app.app_context():
            for peer_id, permitted in (("scan-destination", True), ("scan-unselected", False)):
                federation.save_peer(peer_id, peer_id, f"https://{peer_id}.invalid", "synthetic-only",
                                     {"documents": {"send": permitted}})
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        with sync_playwright() as playwright, browser_fixture.InventoryIntakeBrowserTests._mobile_context(playwright) as context:
            page = context.new_page()
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/")
                       else route.abort())
            page.goto(origin + "/auth/login")
            username = page.locator('input[name="username"]')
            username.fill("owner")
            page.locator('input[name="password"]').fill("test-only-api-password")
            with page.expect_navigation(wait_until="domcontentloaded"):
                page.locator("form").filter(has=username).locator('button[type="submit"]').click()
            page.goto(origin + "/library/book-scan", wait_until="domcontentloaded")
            share = page.locator("form[data-scan-share]")
            expect(share).to_be_hidden()
            page.locator("#book-scan-files").set_input_files({
                "name": "fixture.png", "mimeType": "image/png", "buffer": image_file("Book scanner example"),
            })
            with page.expect_download() as exported:
                page.locator("[data-scan-download]").click()
            exact_pdf = exported.value.path().read_bytes()
            expect(share).to_be_visible()
            expect(share.locator('input[type="file"]')).to_have_count(0)
            expect(share.locator('option[value="scan-unselected"]')).to_have_count(0)
            self.assertFalse(Shares(root).path.exists(), "Creating a PDF must not publish it")
            share.locator('select[name="peer_id"]').select_option("scan-destination")
            share.locator('input[name="confirm"]').check()
            with page.expect_navigation(wait_until="domcontentloaded"):
                share.locator('button[type="submit"]').click()
            offer = page.locator("[data-book-offer]")
            expect(offer).to_be_visible()
            code = offer.get_attribute("data-book-offer")
            with app.app_context():
                self.assertEqual(exact_pdf, Shares(root).read("scan-destination", code)[0])
