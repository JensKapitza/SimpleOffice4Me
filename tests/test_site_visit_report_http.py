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

    def test_foreign_user_gets_no_access_or_leak(self):
        self.client.post("/auth/register", data={"username": "intruder", "password": "intruder-password-123"})
        self.client.post("/auth/login", data={"username": "intruder", "password": "intruder-password-123"})
        preview = self.client.get(f"/site-visits/{self.visit['visit_id']}/report")
        self.assertEqual(404, preview.status_code)
        self.assertNotIn("Druckabfall", preview.get_data(as_text=True))
        created = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                   data={"finding_id": self.finding_id})
        self.assertEqual(404, created.status_code)
        self.assertNotIn(b"%PDF-", created.data)

    def test_csrf_protection_rejects_forged_report_creation(self):
        app.config["TEST_CSRF_PROTECTION"] = True
        try:
            response = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                        data={"finding_id": self.finding_id})
            self.assertIn(response.status_code, (400, 403))
            self.assertNotIn(b"%PDF-", response.data)
            record = self.store.get(self.visit["visit_id"], "reporter")
            self.assertEqual([], record.get("reports", []))
        finally:
            app.config["TEST_CSRF_PROTECTION"] = False

    def test_path_traversal_ids_are_rejected(self):
        for evil in ("../secret", "..%2F..%2Fetc", "/etc/passwd", "reports/x.pdf"):
            preview = self.client.get(f"/site-visits/{evil}/report")
            self.assertIn(preview.status_code, (400, 404), f"visit {evil!r}")
            saved = self.client.get(f"/site-visits/{self.visit['visit_id']}/reports/{evil}.pdf")
            self.assertIn(saved.status_code, (400, 404), f"report {evil!r}")
        normalized = self.client.get("/site-visits//report", follow_redirects=True)
        self.assertEqual(404, normalized.status_code)
        created = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                   data={"finding_id": self.finding_id})
        self.assertEqual(200, created.status_code)
        self.assertTrue(created.data.startswith(b"%PDF-"))

    def test_empty_selection_builds_controlled_empty_report(self):
        response = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf", data={})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.data.startswith(b"%PDF-"))
        record = self.store.get(self.visit["visit_id"], "reporter")
        self.assertEqual(1, len(record.get("reports", [])))
        self.assertEqual([], record["reports"][0]["finding_ids"])

    def test_download_filename_and_no_path_leak_in_errors(self):
        created = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                   data={"finding_id": self.finding_id})
        disposition = created.headers.get("Content-Disposition", "")
        self.assertIn("Ortsterminbericht-", disposition)
        self.assertIn(".pdf", disposition)
        failed = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                  data={"finding_id": "00000000-0000-0000-0000-000000000000"},
                                  follow_redirects=True)
        html = failed.get_data(as_text=True)
        self.assertNotIn(str(self.root), html)
        self.assertNotIn("Traceback", html)

    def test_large_report_with_many_findings_builds(self):
        for number in range(50):
            self.store.add_finding(self.visit["visit_id"], "reporter",
                                   {"title": f"Befund {number:02d} mit etwas längerem Beschreibungstext",
                                    "description": "x" * 500, "status": "confirmed"})
        record = self.store.get(self.visit["visit_id"], "reporter")
        ids = [row["finding_id"] for row in record["findings"]]
        response = self.client.post(f"/site-visits/{self.visit['visit_id']}/report.pdf",
                                    data={"finding_id": ids})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.data.startswith(b"%PDF-"))
        self.assertGreater(len(response.data), 2789)
