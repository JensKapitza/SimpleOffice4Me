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

    def test_setting_priority_keeps_the_edited_job_and_catalog_context(self):
        catalog = FederationCatalog(self.root)
        jobs = {}
        for peer in ("east", "west"):
            catalog.begin_index(peer, "fixture-generation")
            catalog.ingest(peer, "fixture-generation", [
                {"document_id": "same-id", "path": "same-name.pdf", "blob_hash": "a" * 64, "size": 20},
            ])
            catalog.finish_index(peer, "fixture-generation")
            jobs[peer] = catalog.request_download(peer, "same-id", requested_by="federation-ui-admin")["request_id"]

        # Start on a row reached through the filtered catalog, then adjust a
        # different row with the same visible filename. The route, not the
        # old highlight, determines which row should be highlighted now.
        focus = f"/admin/federation?view=transfers&download={jobs['east']}&remote_peer=east&remote_q=same-name"
        page = BeautifulSoup(self.client.get(focus).data, "html.parser")
        self.assertIn("table-primary", page.find(id=f"download-{jobs['east']}")["class"])
        action = f"/admin/federation/queue/{jobs['west']}/priority"
        form = page.find("form", action=action)
        data = {field["name"]: field.get("value", "") for field in form.select("input[name]")}
        updated = self.client.post(action, data={**data, "priority": "12"}, follow_redirects=False)
        destination = urlsplit(updated.headers["Location"])
        self.assertEqual(302, updated.status_code)
        self.assertEqual("download-" + jobs["west"], destination.fragment)
        self.assertEqual({
            "view": ["transfers"], "download": [jobs["west"]], "remote_peer": ["east"], "remote_q": ["same-name"],
        }, parse_qs(destination.query))

        rendered = BeautifulSoup(self.client.get(updated.headers["Location"]).data, "html.parser")
        selected = rendered.find(id=destination.fragment)
        self.assertIn("table-primary", selected["class"])
        self.assertNotIn("table-primary", rendered.find(id=f"download-{jobs['east']}").get("class", []))
        self.assertEqual("12", selected.select_one('input[name="priority"]')["value"])
        back = rendered.select_one('a[href$="#remote-files"]')
        self.assertEqual({"view": ["files"], "remote_peer": ["east"], "remote_q": ["same-name"]},
                         parse_qs(urlsplit(back["href"]).query))
        self.assertEqual(0, catalog.get_request(jobs["east"])["transfer_priority"])
        self.assertEqual("queued", catalog.get_request(jobs["west"])["status"])
        self.assertEqual(0, catalog.get_request(jobs["west"])["attempts"])
        self.assertEqual(2, len(catalog.queue()))
