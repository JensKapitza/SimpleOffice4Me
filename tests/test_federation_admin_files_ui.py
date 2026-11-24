"""Federation's remote file browser distinguishes filtering from missing data."""

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from app.federation_catalog import FederationCatalog
from app.federation_store import FederationStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminFilesUiTests(FederationAdminUiFixture, unittest.TestCase):
    def _results(self, **filters):
        response = self.client.get("/admin/federation", query_string={"view": "files", **filters})
        self.assertEqual(200, response.status_code)
        page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        return page.select_one("#remote-files")

    def test_filtered_empty_results_have_a_reset_not_index_setup_advice(self):
        store = FederationStore(self.root)
        for name in ("office-a", "office-b"):
            store.save_peer(name, name, f"https://{name}.example", "")
        catalog = FederationCatalog(self.root)
        catalog.finish_index("office-b", "empty")
        catalog.begin_index("office-a", "g1")
        catalog.ingest("office-a", "g1", [{
            "document_id": "file-1", "blob_hash": "a" * 64,
            "path": "Existing remote file.pdf", "size": 10,
        }])
        catalog.finish_index("office-a", "g1")

        for language, empty, reset in (
            ("de", "Keine Dateien für diese Filter.", "Filter zurücksetzen"),
            ("en", "No files match these filters.", "Clear filters"),
        ):
            with self.client.session_transaction() as session:
                session["simpleoffice_language"] = language
            for filters in ({"remote_q": "not-in-index"}, {"remote_peer": "office-b"}):
                with self.subTest(language=language, filters=filters):
                    results = self._results(**filters)
                    text = results.get_text(" ", strip=True)
                    self.assertIn(empty, text)
                    self.assertNotIn("Existing remote file.pdf", text)
                    self.assertNotIn("Noch kein Remote-Index vorhanden", text)
                    self.assertNotIn("No remote index present", text)
                    link = results.find("a", string=reset)
                    self.assertIsNotNone(link)
                    url = urlsplit(link["href"])
                    self.assertEqual("/admin/federation", url.path)
                    self.assertEqual({"view": ["files"]}, parse_qs(url.query))
                    self.assertEqual("remote-files", url.fragment)
                    after = self.client.get(link["href"])
                    result = BeautifulSoup(after.get_data(as_text=True), "html.parser").select_one("#remote-files")
                    self.assertIn("Existing remote file.pdf", result.get_text(" ", strip=True))
            self.assertIn("Existing remote file.pdf", self._results(remote_q="Existing").get_text())

    def test_reset_preserves_independent_local_file_context(self):
        document = {"document_id": "local-file-1", "last_path": "Selected local file.pdf"}
        with patch(
            "app.federation_admin._document_blob",
            return_value=(document, {"size": 10}, "b" * 64),
        ):
            results = self._results(remote_q="hidden", q="Local + Q2", document_id=document["document_id"])
        link = results.find("a", string="Filter zurücksetzen")
        self.assertIsNotNone(link)
        self.assertEqual(
            {"view": ["files"], "q": ["Local + Q2"], "document_id": ["local-file-1"]},
            parse_qs(urlsplit(link["href"]).query),
        )

    def test_empty_unfiltered_results_link_to_peer_index_without_claiming_no_sync(self):
        store = FederationStore(self.root)
        store.save_peer("office-b", "Office B", "https://office-b.example", "")
        FederationCatalog(self.root).finish_index("office-b", "empty")
        for language, empty, action in (
            ("de", "Keine entfernten Dateien im Index.", "Zur Peer-Liste"),
            ("en", "No remote files in the index.", "Go to peers"),
        ):
            with self.subTest(language=language):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = language
                results = self._results()
                self.assertIn(empty, results.get_text(" ", strip=True))
                link = results.find("a", string=action)
                self.assertIsNotNone(link)
                self.assertEqual({"view": ["peers"]}, parse_qs(urlsplit(link["href"]).query))
