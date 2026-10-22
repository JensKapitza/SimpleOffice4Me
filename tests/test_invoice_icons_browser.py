"""Browser checks for invoice icon contrast, labels and narrow layouts."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path

from werkzeug.serving import make_server

from app import app
from app.db import ensure_auth_database
from app.contact_store import ContactStore
from app.document_store import CONTROL_DIR


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install the optional web-export extra")
class InvoiceIconsBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        credentials = {"username": "invoice-icon-browser", "password": "browser-test-password"}
        self.client.post("/auth/register", data=credentials)
        self.client.post("/auth/login", data=credentials)
        contact = ContactStore(self.root).upsert(
            {"display_name": "Icon Browser Customer"}, "invoice-icon-browser",
        )
        self.contact_id = contact["contact_id"]
        self._write_invoice()
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

    def _write_invoice(self):
        issue = date.today()
        due = issue + timedelta(days=14)
        invoice = {
            "invoice_id": "invoice-icon-test",
            "invoice_number": "2026-ICON-0001",
            "contact_id": self.contact_id,
            "status": "open",
            "issue_date": issue.isoformat(),
            "service_date": issue.isoformat(),
            "due_date": due.isoformat(),
            "currency": "EUR",
            "payment_terms": "14 Tage",
            "buyer": {"name": "Icon Browser Customer", "label": "Icon Browser Customer\nExample Street 1"},
            "seller": {
                "name": "Example Ltd", "street": "Street 1", "postal": "12345",
                "city": "Berlin", "country": "DE", "iban": "",
            },
            "lines": [],
            "totals": {
                "net": "100.00", "tax": "19.00", "gross": "119.00", "due": "119.00",
                "vat_groups": {"19": {"basis": "100.00", "tax": "19.00"}},
            },
            "payments": [], "write_offs": [], "credit_notes": [],
            "zugferd": {
                "status": "validated", "version": "2.5.2", "profile": "EN16931",
                "pdfa_pipeline": "pdfa3_created", "validation": {"details": []},
            },
            "document_id": "document-icon-test",
        }
        directory = self.root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "invoice-icon-test.json").write_text(json.dumps(invoice), encoding="utf-8")

    def test_invoice_actions_are_labeled_and_visible_on_mobile_in_dark_mode(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            scenarios = (
                (playwright.chromium, {"viewport": {"width": 1280, "height": 900}}, "desktop"),
                (playwright.chromium, playwright.devices["Pixel 7"], "android"),
                (playwright.webkit, playwright.devices["iPhone 14"], "iphone"),
            )
            for browser_type, options, label in scenarios:
                with self.subTest(platform=label):
                    self._check_invoice_pages(browser_type, options)

    def _check_invoice_pages(self, browser_type, options):
        browser = browser_type.launch()
        context_options = {**options, "color_scheme": "dark"}
        context = browser.new_context(**context_options)
        context.add_cookies([{
            "name": app.config.get("SESSION_COOKIE_NAME", "session"),
            "value": self.cookie,
            "url": self.base_url,
        }])
        try:
            page = context.new_page()
            pages = (
                ("/documents/business/invoices", "h1 .fa-copy"),
                (f"/documents/business/contacts/{self.contact_id}/billing", ".fa-file-circle-plus"),
                (f"/documents/business/contacts/{self.contact_id}/invoice", "h1 .fa-file-circle-plus"),
                ("/documents/business/invoices/invoice-icon-test", "h1 .fa-file-invoice"),
            )
            for url, icon_selector in pages:
                page.goto(self.base_url + url)
                page.wait_for_function("document.documentElement.dataset.bsTheme === 'dark'")
                icon = page.locator(icon_selector).first
                self.assertTrue(icon.is_visible(), f"icon {icon_selector} not visible at {url}")
                self.assertEqual("true", icon.get_attribute("aria-hidden"))
                self.assertNotEqual("rgba(0, 0, 0, 0)", icon.evaluate("node => getComputedStyle(node).color"))
                viewport_width = page.evaluate("window.innerWidth")
                bounds = icon.bounding_box()
                self.assertIsNotNone(bounds)
                self.assertLessEqual(bounds["x"] + bounds["width"], viewport_width + 1)
                scroller = page.locator(".table-responsive").first
                self.assertIn(
                    scroller.evaluate("node => getComputedStyle(node).overflowX"),
                    {"auto", "scroll"},
                )
            self.assertTrue(page.get_by_role("link", name="Herunterladen").first.is_visible())
            self.assertTrue(page.get_by_role("link", name="PDF").first.is_visible())
        finally:
            context.close()
            browser.close()

    def test_invoice_draft_form_reflows_and_recalculates_at_mobile_widths(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 430, "height": 900})
            context.add_cookies([{
                "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                "value": self.cookie,
                "url": self.base_url,
            }])
            try:
                page = context.new_page()
                url = f"{self.base_url}/documents/business/contacts/{self.contact_id}/invoice"
                for width in (320, 360, 390, 430, 1280):
                    with self.subTest(width=width):
                        page.set_viewport_size({"width": width, "height": 900})
                        page.goto(url)
                        page.locator("#invoice-lines").wait_for(state="attached")
                        self.assertLessEqual(
                            page.evaluate("document.documentElement.scrollWidth"), width + 1,
                            "invoice form causes page-level horizontal scrolling",
                        )
                        if width <= 767:
                            self.assertFalse(page.locator("#invoice-lines thead").is_visible())
                        else:
                            self.assertTrue(page.locator("#invoice-lines thead").is_visible())

                        page.get_by_role("button", name="+ freie Position").click()
                        row = page.locator("#invoice-lines tbody tr").first
                        row.locator(".line-description").fill("Beratung")
                        row.locator(".line-qty").fill("2")
                        row.locator(".line-net").fill("10")
                        row.locator(".line-vat").fill("19")
                        self.assertIn("20,00", page.locator("#sum-net").inner_text())
                        self.assertIn("3,80", page.locator("#sum-tax").inner_text())
                        self.assertIn("23,80", page.locator("#sum-gross").inner_text())
                        for selector in (".line-description", ".line-qty", "select[name='line_unit']",
                                         ".line-net", ".line-vat", ".move-line-up", ".remove-line"):
                            bounds = row.locator(selector).bounding_box()
                            self.assertIsNotNone(bounds, f"{selector} is not visible at {width}px")
                            self.assertGreaterEqual(bounds["x"], -1, f"{selector} extends left at {width}px: {bounds}")
                            self.assertLessEqual(
                                bounds["x"] + bounds["width"], width + 1,
                                f"{selector} extends right at {width}px: {bounds}",
                            )
                        self.assertEqual("Menge", row.locator(".line-qty").get_attribute("aria-label"))
                        if width <= 767:
                            label = page.locator("#invoice-lines td:nth-child(2)").evaluate(
                                "node => getComputedStyle(node, '::before').content"
                            )
                            self.assertIn("Menge", label)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
