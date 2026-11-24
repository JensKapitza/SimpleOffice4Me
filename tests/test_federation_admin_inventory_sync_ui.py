"""Administrator copy follows the actual inventory sync outcome."""

import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from bs4 import BeautifulSoup

from app.federation_catalog import FederationCatalog
from app.federation_store import FederationStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminInventorySyncUiTests(FederationAdminUiFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.catalog = FederationCatalog(self.root)
        FederationStore(self.root).save_peer(
            "office-b", "Office B", "https://office-b.example", "",
            {"inventory": {"receive": True}},
        )

    def _seed_local_copy(self):
        self.catalog.begin_inventory_index("office-b", "g1")
        count = self.catalog.ingest_inventory("office-b", "g1", [{
            "identifier": "9783161484100", "identifier_type": "isbn", "display_name": "Sample book",
        }])
        self.assertEqual(1, count)
        self.catalog.finish_inventory_index("office-b", "g1")

    def _refresh(self, language, error):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        view = self.client.get("/admin/federation?view=peers")
        html = BeautifulSoup(view.get_data(as_text=True), "html.parser")
        path = "/admin/federation/peers/office-b/sync-inventory-index"
        form = html.find("form", action=path)
        self.assertIsNotNone(form)
        fields = {control["name"]: control["value"] for control in form.select('input[type="hidden"]')}
        with patch("app.federation_download_worker._json_request", side_effect=error) as transport:
            result = self.client.post(path, data=fields, follow_redirects=True)
            transport.assert_called_once()
        self.assertEqual(200, result.status_code)
        return BeautifulSoup(result.get_data(as_text=True), "html.parser").get_text(" ", strip=True)

    def test_remote_refusal_reports_only_cleanup_that_occurred(self):
        for language, refusal, removed in (
            ("de", "Inventarabruf verweigert (HTTP 403).", "Lokale Inventareinträge wurden entfernt."),
            ("en", "Inventory request denied (HTTP 403).", "Local inventory entries were removed."),
        ):
            for cached in (True, False):
                with self.subTest(language=language, cached=cached):
                    if cached:
                        self._seed_local_copy()
                    page = self._refresh(language, HTTPError(
                        "https://example.invalid/inventory", 403, "internal-refusal-detail", {}, None,
                    ))
                    self.assertIn(refusal, page)
                    self.assertEqual(cached, removed in page)
                    self.assertNotIn("bleibt erhalten", page)
                    self.assertNotIn("is preserved", page)
                    self.assertNotIn("internal-refusal-detail", page)
                    self.assertEqual([], self.catalog.remote_inventory_items("office-b"))
                    state = self.catalog.inventory_state("office-b")
                    self.assertIsNone(state["last_index_at"])
                    self.assertEqual("", state["last_index_error"])

    def test_transient_failure_keeps_rows_without_promise_or_internal_details(self):
        self._seed_local_copy()
        last_success = self.catalog.inventory_state("office-b")["last_index_at"]
        for language, error in (
            ("de", "Inventarindex konnte nicht aktualisiert werden."),
            ("en", "Could not refresh the inventory index."),
        ):
            with self.subTest(language=language):
                page = self._refresh(language, URLError("internal-transient-detail"))
                self.assertIn(error, page)
                self.assertNotIn("HTTP 403", page)
                self.assertNotIn("bleibt erhalten", page)
                self.assertNotIn("is preserved", page)
                self.assertNotIn("internal-transient-detail", page)
                self.assertEqual(1, len(self.catalog.remote_inventory_items("office-b")))
                state = self.catalog.inventory_state("office-b")
                self.assertEqual(last_success, state["last_index_at"])
                self.assertTrue(state["last_index_error"])
