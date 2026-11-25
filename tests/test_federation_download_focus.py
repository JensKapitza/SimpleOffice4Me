"""Link a real Files form result to the exact existing download in Transfers."""

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from app.federation_catalog import FederationCatalog
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationDownloadFocusTests(FederationAdminUiFixture, unittest.TestCase):
    def test_queue_confirmation_keeps_filters_and_targets_exact_peer_request(self):
        catalog = FederationCatalog(self.root)
        for peer in ("west", "east"):
            catalog.begin_index(peer, "fixture-generation")
            catalog.ingest(peer, "fixture-generation", [
                {"document_id": "same-id", "path": "same-name.pdf", "blob_hash": "a" * 64, "size": 20},
            ])
            catalog.finish_index(peer, "fixture-generation")
        old = catalog.request_download("east", "same-id", requested_by="federation-ui-admin")
        files = self.client.get("/admin/federation?view=files&remote_q=same-name")
        page = BeautifulSoup(files.data, "html.parser")
        form = next(f for f in page.select('form[action$="/remote/action"]')
                    if f.select_one('[name=action][value=download]') and f.select_one('[name=peer_id][value=west]'))
        form_values = {i["name"]: i.get("value") for i in form.select('input[name]')}
        confirmed = self.client.post(form["action"], data=form_values, follow_redirects=True)
        self.assertEqual(200, confirmed.status_code)
        confirmed_page = BeautifulSoup(confirmed.data, "html.parser")
        self.assertEqual("same-name", confirmed_page.select_one("#remote-file-filter")["value"])
        new = next(row for row in catalog.queue() if row["peer_id"] == "west")
        self.assertNotEqual(new["request_id"], old["request_id"])
        link = confirmed_page.select_one("[data-federation-download-focus]")["href"]
        destination = urlsplit(link)
        self.assertEqual("download-" + new["request_id"], destination.fragment)
        self.assertEqual({"view": ["transfers"], "download": [new["request_id"]], "remote_q": ["same-name"]},
                         parse_qs(destination.query))
        again = self.client.post(form["action"], data=form_values, follow_redirects=True)
        self.assertEqual(2, len(catalog.queue()))
        repeated_link = BeautifulSoup(again.data, "html.parser").select_one("[data-federation-download-focus]")["href"]
        self.assertEqual(link, repeated_link)

        with patch.object(FederationCatalog, "queue", return_value=[old]):
            focused = self.client.get(link)
        focused_page = BeautifulSoup(focused.data, "html.parser")
        row = focused_page.find(id=destination.fragment)
        self.assertIsNotNone(row)
        self.assertIn("west", row.get_text())
        self.assertNotIn("east", row.get_text())
        self.assertEqual(1, len(focused_page.find_all(id=destination.fragment)))
        self.assertIn(old["request_id"], focused.get_data(as_text=True))
        self.assertIsNone(BeautifulSoup(self.client.get(
            f"/admin/federation?view=files&download={uuid4()}",
        ).data, "html.parser").select_one("[data-federation-download-focus]"))
