"""Local file transfers keep the filtered Files view without contacting peers."""

import io
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup

from app.document_store import DocumentStore
from app.federation_store import FederationStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminLocalSearchTests(FederationAdminUiFixture, unittest.TestCase):
    query = "März + Q2 & Team"
    filename = "März + Q2 & Team.txt"
    unrelated = "unrelated-note.txt"

    def setUp(self):
        super().setUp()
        documents = DocumentStore(self.root)
        document = documents.import_upload(io.BytesIO(b"selected"), self.filename, "federation-ui-admin")
        documents.import_upload(io.BytesIO(b"unrelated"), self.unrelated, "federation-ui-admin")
        self.document_id = document["document_id"]
        self.digest = document["sha256"]
        self.store = FederationStore(self.root)
        for peer in ("source", "target"):
            self.store.save_peer(
                peer, peer, f"https://{peer}.example.test", "", {"documents": {"send": True}},
            )
        self.filters = {"view": "files", "document_id": self.document_id, "q": self.query}

    def form(self, action):
        path = f"/admin/federation/documents/{self.document_id}/{action}"
        response = self.client.get("/admin/federation?" + urlencode(self.filters))
        self.assertEqual(200, response.status_code)
        page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        form = page.find("form", action=path)
        self.assertIsNotNone(form)
        return path, {field["name"]: field["value"] for field in form.select("input[type=hidden][name]")}

    def assert_returned_to_filtered_file(self, response):
        self.assertEqual(302, response.status_code)
        location = urlsplit(response.location)
        self.assertEqual(("", "", "/admin/federation"), (location.scheme, location.netloc, location.path))
        self.assertEqual({key: [value] for key, value in self.filters.items()}, parse_qs(location.query))
        page = BeautifulSoup(self.client.get(response.location).get_data(as_text=True), "html.parser")
        self.assertEqual(self.query, page.select_one('main input[type=search][name=q]')["value"])
        self.assertEqual(self.filename, page.select_one(".list-group a.active strong").get_text())
        self.assertNotIn(self.unrelated, page.get_text())
        return page

    def test_queue_only_and_immediate_send_keep_local_search(self):
        path, form = self.form("send")
        with patch("app.federation_admin.push_blob_to_peer", return_value={"status": "complete"}) as push:
            for start_now in ("0", "1"):
                with self.subTest(start_now=start_now):
                    response = self.client.post(path, data={
                        **form, "target_peer": "target", "operation": "COPY", "start_now": start_now,
                    })
                    self.assert_returned_to_filtered_file(response)
                    self.assertEqual(int(start_now), push.call_count)
        jobs = self.store.list_transfers()
        self.assertEqual(2, len(jobs))
        self.assertEqual({self.digest}, {job["blob_hash"] for job in jobs})
        self.assertIn(push.call_args.args[1], {job["transfer_id"] for job in jobs})

    def test_coordination_keeps_local_search_and_original_transfer_arguments(self):
        path, form = self.form("orchestrate")
        with patch("app.federation_admin.orchestrate_third_party",
                   return_value={"transfer_id": "synthetic"}) as coordinate:
            response = self.client.post(path, data={
                **form, "source_peer": "source", "target_peer": "target", "operation": "REPLICATE",
            })
        coordinate.assert_called_once_with(str(self.root), "source", "target", self.digest, operation="REPLICATE")
        self.assert_returned_to_filtered_file(response)

    def test_rejected_send_keeps_local_search_without_creating_a_transfer(self):
        path, form = self.form("send")
        with patch("app.federation_admin.push_blob_to_peer") as push:
            response = self.client.post(path, data={
                **form, "target_peer": "not-configured", "operation": "COPY", "start_now": "1",
            })
        push.assert_not_called()
        self.assertEqual([], self.store.list_transfers())
        page = self.assert_returned_to_filtered_file(response)
        self.assertIn("Ziel-Peer ist nicht aktiv", page.get_text())

    def test_local_search_on_redirect_is_bounded_and_blank_is_omitted(self):
        path, form = self.form("send")
        return_key = "_return_local_q"
        for raw, expected in (("   ", None), ("  " + "é" * 161 + "  ", "é" * 160)):
            with self.subTest(expected=expected):
                response = self.client.post(path, data={**form, return_key: raw})
                self.assertEqual(302, response.status_code)
                values = parse_qs(urlsplit(response.location).query)
                self.assertEqual([expected] if expected else None, values.get("q"))
        self.assertEqual([], self.store.list_transfers())
