"""Browser-level creation and persisted re-download of a large site-visit report."""
from __future__ import annotations

import importlib.util
import io
import re
import tempfile
import threading
import unittest
from pathlib import Path

from pypdf import PdfReader
from werkzeug.serving import make_server

from app import app
from app import db as database
from app.site_visit_store import SiteVisitStore


# Product UI renders English by default and German when the session pins
# ``simpleoffice_language`` to ``de``. The saved-report link is located by
# its stable ``/reports/`` href; the label assertion accepts both locales.
_SAVED_REPORT_LABEL_RE = re.compile(r"(Download PDF again|PDF erneut herunterladen)")
_COMPILE_REPORT_RE = re.compile(r"(Compile report|Bericht zusammenstellen)")
_GENERATE_PDF_RE = re.compile(r"(Generate PDF|PDF erzeugen)")


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright browser extra is required")
class SiteVisitReportBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        self.addCleanup(app.config.update, self.saved)
        app.config.update(
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "report-browser", "password": "site-visit-browser-passphrase"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "report-browser", "password": "site-visit-browser-passphrase"},
        )
        cookie = self.client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value

        # Browser requests use the real synchronizer-token check; the token is
        # rendered into the report form and submitted by the UI.
        app.config["TEST_CSRF_PROTECTION"] = True
        self.store = SiteVisitStore(self.root)
        self.visit = self.store.create("report-browser", {"title": "Synthetic report download QA"})
        for number in range(1, 33):
            self.store.add_finding(
                self.visit["visit_id"],
                "report-browser",
                {
                    "title": f"Synthetic finding {number:02d}",
                    "description": (f"Synthetic acceptance detail {number:02d}. " * 18).strip(),
                    "status": "confirmed",
                    "include_in_report": "1",
                },
            )

        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server_thread.join(timeout=5)

    def test_large_report_download_and_saved_redownload_work_in_browser(self) -> None:
        from playwright.sync_api import expect, sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True)
            context.add_cookies(
                [
                    {
                        "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                        "value": self.cookie,
                        "url": self.base_url,
                    }
                ]
            )
            try:
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                preview_url = f"{self.base_url}/site-visits/{self.visit['visit_id']}/report"
                page.goto(preview_url)
                expect(page.get_by_role("heading", name=_COMPILE_REPORT_RE)).to_be_visible()
                checkboxes = page.locator("input[name='finding_id']")
                self.assertEqual(32, checkboxes.count())
                for checkbox in checkboxes.all():
                    checkbox.check()

                created_path = Path(self.temp.name) / "created-report.pdf"
                with page.expect_download(timeout=70000) as download_info:
                    page.get_by_role("button", name=_GENERATE_PDF_RE).click()
                download_info.value.save_as(created_path)
                created_bytes = created_path.read_bytes()
                self.assertGreater(len(created_bytes), 1000)
                self.assertTrue(created_bytes.startswith(b"%PDF"))
                reader = PdfReader(io.BytesIO(created_bytes))
                self.assertGreaterEqual(len(reader.pages), 2)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                self.assertIn("Synthetic finding 01", text)
                self.assertIn("Synthetic finding 32", text)

                page.goto(f"{self.base_url}/site-visits/{self.visit['visit_id']}")
                saved_report_link = page.locator('a[href*="/reports/"]')
                expect(saved_report_link).to_have_count(1)
                expect(saved_report_link).to_be_visible()
                self.assertRegex(saved_report_link.inner_text(), _SAVED_REPORT_LABEL_RE)
                saved_path = Path(self.temp.name) / "saved-report.pdf"
                with page.expect_download(timeout=70000) as saved_info:
                    saved_report_link.click()
                saved_info.value.save_as(saved_path)
                self.assertEqual(created_bytes, saved_path.read_bytes())
                self.assertEqual([], page_errors)
            finally:
                context.close()
                browser.close()

    def test_saved_report_link_labels_render_in_english_and_german(self) -> None:
        """Saved-report re-download link exists in both locales (no browser)."""
        entry = self.store.save_report_pdf(
            self.visit["visit_id"], "report-browser", b"%PDF-1.4\n%EOF\n", []
        )
        detail_url = f"/site-visits/{self.visit['visit_id']}"
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        english = self.client.get(detail_url)
        self.assertEqual(200, english.status_code)
        english_body = english.get_data(as_text=True)
        self.assertIn("Download PDF again", english_body)
        self.assertIn(f"/reports/{entry['report_id']}.pdf", english_body)
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        german = self.client.get(detail_url)
        self.assertEqual(200, german.status_code)
        german_body = german.get_data(as_text=True)
        self.assertIn("PDF erneut herunterladen", german_body)
        self.assertIn(f"/reports/{entry['report_id']}.pdf", german_body)


if __name__ == "__main__":
    unittest.main()
