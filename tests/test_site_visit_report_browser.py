"""Browser-level creation and persisted re-download of a large site-visit report."""
from __future__ import annotations

import importlib.util
import io
import tempfile
import threading
import unittest
from pathlib import Path

from pypdf import PdfReader
from werkzeug.serving import make_server

from app import app
from app import db as database
from app.site_visit_store import SiteVisitStore


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
        client = app.test_client()
        client.post(
            "/auth/register",
            data={"username": "report-browser", "password": "site-visit-browser-passphrase"},
        )
        client.post(
            "/auth/login",
            data={"username": "report-browser", "password": "site-visit-browser-passphrase"},
        )
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
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
                expect(page.get_by_role("heading", name="Bericht zusammenstellen")).to_be_visible()
                checkboxes = page.locator("input[name='finding_id']")
                self.assertEqual(32, checkboxes.count())
                for checkbox in checkboxes.all():
                    checkbox.check()

                created_path = Path(self.temp.name) / "created-report.pdf"
                with page.expect_download(timeout=70000) as download_info:
                    page.get_by_role("button", name="PDF erzeugen").click()
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
                saved_report_link = page.get_by_role("link", name="PDF erneut herunterladen")
                expect(saved_report_link).to_be_visible()
                saved_path = Path(self.temp.name) / "saved-report.pdf"
                with page.expect_download(timeout=70000) as saved_info:
                    saved_report_link.click()
                saved_info.value.save_as(saved_path)
                self.assertEqual(created_bytes, saved_path.read_bytes())
                self.assertEqual([], page_errors)
            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
