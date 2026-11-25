"""Real advanced search when the local index is incomplete."""
from __future__ import annotations

import io
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash

from app import app
from app.db import get_db
from app.document_store import DocumentStore

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


class SearchIndexStatusUiTests(PublicHttpTestCase):
    search = "/documents/search"

    def store(self):
        return DocumentStore(app.config["DOCUMENT_ROOT"])

    def results(self, q, *, page=1, cursor=None, client=None):
        args = {"q": q, "page": page}
        if cursor:
            args["cursor"] = cursor
        response = (client or self.client).get(self.search, query_string=args)
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        return BeautifulSoup(response.data, "html.parser")

    def upload(self, name, content):
        document = self.store().import_upload(io.BytesIO(content), name, "owner")
        return f"/documents/{document['document_id']}"

    def test_incomplete_worker_reports_do_not_claim_no_matches_or_discard_partial_results(self):
        path = self.upload("synthetic-result.txt", b"OcrFixtureWord")
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        for state, errors, heading in (
            ("failed", 0, "Indexing did not finish"),
            ("recovery-needed", 0, "Index recovery needs attention"),
            ("completed", 2, "Indexing reported errors"),
            ("watching", 2, "Indexing reported errors"),
        ):
            with self.subTest(state=state, errors=errors):
                self.store().set_scan_status({"state": state, "errors": errors, "error_type": "SYNTHETIC-DONT-RENDER",
                                              "v2_reconcile": {"location": "/private-synthetic-path"}})
                with (
                    patch.object(DocumentStore, "scan", side_effect=AssertionError("GET cannot scan")),
                    patch.object(DocumentStore, "refresh_missing_text", side_effect=AssertionError("GET cannot backfill")),
                ):
                    partial = self.results("text:OcrFixtureWord")
                    empty = self.results("text:NoSuchFixture")
                for doc, query in ((partial, "text:OcrFixtureWord"), (empty, "text:NoSuchFixture")):
                    banner = doc.select_one("#document-search-index-status")
                    self.assertIsNotNone(banner)
                    self.assertIn(heading, banner.get_text())
                    self.assertIn("results may be incomplete", banner.get_text())
                    reload_link = banner.select_one('a[data-search-refresh]')
                    self.assertEqual({"q": [query]}, parse_qs(urlsplit(reload_link["href"]).query))
                    self.assertNotIn("SYNTHETIC-DONT-RENDER", doc.get_text())
                    self.assertNotIn("/private-synthetic-path", doc.get_text())
                self.assertIsNotNone(partial.select_one(f'.list-group a[href="{path}"]'))
                self.assertEqual("No indexed results on this page.", empty.select_one("[data-search-empty]").get_text(strip=True))

    def test_healthy_empty_search_and_running_warning_are_distinct(self):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        for state in ("completed", "watching"):
            with self.subTest(state=state):
                self.store().set_scan_status({"state": state, "errors": 0})
                page = self.results("text:UnseenFixture")
                self.assertIsNone(page.select_one("#document-search-index-status"))
                self.assertEqual("Keine Treffer.", page.select_one("[data-search-empty]").get_text(strip=True))
        self.store().set_scan_status({"state": "running", "errors": 0})
        page = self.results("text:UnseenFixture")
        self.assertEqual(1, len(page.select("#document-search-index-status")))
        self.assertIn("Die letzte Meldung", page.select_one("#document-search-index-status").get_text())
        invalid = self.results("text:(")
        self.assertIsNone(invalid.select_one("[data-search-empty]"))

    def test_roles_show_only_their_existing_administration_access(self):
        self.store().set_scan_status({"state": "recovery-needed"})
        guest = app.test_client()
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,0)",
                       ("viewer", generate_password_hash("test-only-api-password")))
            db.commit()
        self._login(guest, "viewer")
        member = self.results("fixture", client=guest).select_one("#document-search-index-status")
        self.assertIsNone(member.select_one('a[href*="/admin/"]'))
        self.assertTrue("administrator" in member.get_text().casefold())
        admin = self.results("fixture").select_one("#document-search-index-status")
        self.assertIsNotNone(admin.select_one('a[href="/admin/settings/server#server-bgindex"]'))
        anonymous = app.test_client().get(self.search)
        self.assertEqual(302, anonymous.status_code)
        self.assertNotIn(b"document-search-index-status", anonymous.data)
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) "
                       "SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='viewer'")
            db.commit()
        denied = guest.get(self.search)
        self.assertEqual(403, denied.status_code)
        self.assertNotIn(b"document-search-index-status", denied.data)

    def test_refresh_reuses_the_canonical_search_page_and_valid_cursor(self):
        for i in range(27):
            self.upload(f"search-page-{i}.txt", f"CursorFixture item {i}".encode())
        query = "text:CursorFixture"
        first = self.results(query)
        next_link = first.select_one("a[rel=next]")["href"]
        qargs = parse_qs(urlsplit(next_link).query)
        self.assertEqual(["2"], qargs["page"])
        self.assertTrue(qargs["cursor"][0])
        self.store().set_scan_status({"state": "failed"})
        page = self.results(query, page=2, cursor=qargs["cursor"][0])
        reload_link = page.select_one('a[data-search-refresh]')["href"]
        self.assertEqual(qargs, parse_qs(urlsplit(reload_link).query))
        self.assertIsNotNone(page.select_one("a[rel=prev]"))
        bad = self.results(query, page=2, cursor="not-a-valid-continuation")
        reload_link = bad.select_one('a[data-search-refresh]')["href"]
        self.assertEqual({"q": [query], "page": ["2"]}, parse_qs(urlsplit(reload_link).query))
