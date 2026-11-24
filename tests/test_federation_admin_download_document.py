"""Finished downloads open the local document through its normal authorized route."""

import io
import unittest
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.document_store import DocumentStore
from app.federation_catalog import FederationCatalog
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminDownloadDocumentTests(FederationAdminUiFixture, unittest.TestCase):
    transfers = "/admin/federation?view=transfers&q=matches-no-local-document"

    def test_only_completed_rows_with_visible_live_local_documents_link_to_them(self):
        store = DocumentStore(self.root)
        actor = "federation-ui-admin"
        document = store.import_upload(io.BytesIO(b"visible local download"), "download.txt", actor)
        archived = store.import_upload(io.BytesIO(b"deleted local download"), "old.txt", actor)
        store.soft_delete_document(archived["document_id"], actor)
        private_file = self.root / "email" / ("a" * 32) / "other-account.txt"
        private_file.parent.mkdir(parents=True)
        private_file.write_text("synthetic private mail", encoding="utf-8")
        private = store.scan_and_get_document(private_file)

        states = {
            "linked": ("complete", document["document_id"]),
            "unqueued": ("queued", document["document_id"]),
            "absent": ("complete", "missing-local-doc"),
            "deleted": ("complete", archived["document_id"]),
            "restricted": ("complete", private["document_id"]),
            "legacy": ("complete", ""),
        }
        catalog = FederationCatalog(self.root)
        peer = "office-b"
        catalog.begin_index(peer, "gen-1")
        catalog.ingest(peer, "gen-1", [
            {"document_id": row, "path": f"remote/{row}.txt", "blob_hash": f"{i:064x}", "size": 20}
            for i, row in enumerate(states, 1)
        ])
        catalog.finish_index(peer, "gen-1")
        requests = {}
        for row, (status, local_document_id) in states.items():
            entry = catalog.request_download(peer, row, requested_by=actor)
            requests[row] = entry["request_id"]
            catalog.update_request(entry["request_id"], status=status, local_document_id=local_document_id)

        before = catalog.queue()
        for language, label in (("de", "Lokales Dokument öffnen"), ("en", "Open local document")):
            with self.subTest(language=language):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = language
                page = self.client.get(self.transfers)
                self.assertEqual(200, page.status_code)
                table = BeautifulSoup(page.get_data(as_text=True), "html.parser")
                for row, request_id in requests.items():
                    with self.subTest(row=row):
                        form = table.find("form", action=f"/admin/federation/queue/{request_id}/priority")
                        self.assertIsNotNone(form)
                        links = form.find_parent("tr").find_all("a")
                        if row == "linked":
                            self.assertEqual(1, len(links))
                            target = urlsplit(links[0]["href"])
                            self.assertEqual(("", "", f"/documents/{document['document_id']}"),
                                             (target.scheme, target.netloc, target.path))
                            self.assertEqual(label, links[0].get_text(strip=True))
                            opened = self.client.get(links[0]["href"])
                            self.assertEqual(200, opened.status_code)
                            self.assertIn("download.txt", opened.get_data(as_text=True))
                        else:
                            self.assertEqual([], links)
                for unavailable in (private["document_id"], archived["document_id"], "missing-local-doc"):
                    self.assertNotIn(f"/documents/{unavailable}", page.get_data(as_text=True))
        self.assertEqual(before, catalog.queue())
