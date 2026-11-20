"""Blackbox regression: site-visit reports create and download (Issue #759)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.site_visit_store import SiteVisitStore


class SiteVisitReportHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "reporter", "password": "reporter-password-123"})
        self.client.post("/auth/login", data={"username": "reporter", "password": "reporter-password-123"})
        self.root = base / "documents"
        self.store = SiteVisitStore(self.root)
        self.visit = self.store.create("reporter", {"title": "Heizungswartung", "customer": "Muster GmbH",
                                                    "visit_date": "2026-11-20"})
        record = self.store.add_finding(self.visit["visit_id"], "reporter",
                                        {"title": "Druckabfall", "status": "confirmed"})
        self.finding_id = record["findings"][-1]["finding_id"]

    def tearDown(self):
        app.config.update(self.saved)

    def test_preview_renders_with_finding(self):
        response = self.client.get(f"/site-visits/{self.visit['visit_id']}/report")
        self.assertEqual(200, response.status_code)
        self.assertIn("Druckabfall", response.get_data(as_text=True))

    def test_create_returns_valid_pdf_and_saves_version(self):
        response = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                    data={"finding_id": self.finding_id})
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.content_type)
        self.assertTrue(response.data.startswith(b"%PDF-"))
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        record = self.store.get(self.visit["visit_id"], "reporter")
        self.assertEqual(1, len(record.get("reports", [])))

    def test_saved_report_download_is_byte_identical(self):
        created = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                   data={"finding_id": self.finding_id})
        report_id = self.store.get(self.visit["visit_id"], "reporter")["reports"][-1]["report_id"]
        downloaded = self.client.get(f"/site-visits/{self.visit['visit_id']}/reports/{report_id}.pdf")
        self.assertEqual(200, downloaded.status_code)
        self.assertEqual("application/pdf", downloaded.content_type)
        self.assertEqual(created.data, downloaded.data)
        self.assertIn("no-store", downloaded.headers.get("Cache-Control", ""))

    def test_invalid_selection_fails_controlled_without_partial_save(self):
        response = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                    data={"finding_id": "00000000-0000-0000-0000-000000000000"},
                                    follow_redirects=True)
        self.assertEqual(200, response.status_code)
        record = self.store.get(self.visit["visit_id"], "reporter")
        self.assertEqual([], record.get("reports", []))
        missing = self.client.get(f"/site-visits/{self.visit['visit_id']}/reports/does-not-exist.pdf")
        self.assertEqual(404, missing.status_code)

    def test_report_routes_require_login(self):
        anonymous = app.test_client()
        preview = anonymous.get(f"/site-visits/{self.visit['visit_id']}/report", follow_redirects=False)
        self.assertIn(preview.status_code, (302, 401, 403))
        created = anonymous.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                 data={"finding_id": self.finding_id}, follow_redirects=False)
        self.assertIn(created.status_code, (302, 401, 403))
        self.assertNotIn(b"%PDF-", created.data)
