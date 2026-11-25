"""Completed direct receipts are distinct from Documents catalogue imports."""

import unittest
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from app import app
from app.federation_catalog import FederationCatalog
from app.federation_store import FederationStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminDirectReceiptTests(FederationAdminUiFixture, unittest.TestCase):
    transfers = "/admin/federation?view=transfers"

    def test_completed_direct_receipt_opens_generic_catalogue_without_inferred_source(self):
        store = FederationStore(self.root)
        for name, enabled, token, receive in (
            ("ready", True, "fixture-credential", True),
            ("disabled", False, "fixture-credential", True),
            ("no-receive", True, "fixture-credential", False),
            ("no-token", True, "", True),
            ("legacy", True, "fixture-credential", None),
            ("other", True, "fixture-credential", True),
        ):
            with app.app_context():
                store.save_peer(
                    name, name, "https://federation-fixture.example.test", token,
                    enabled=enabled, policy={"documents": {"receive": receive}} if receive is not None else {},
                )
        states = (
            ("ready", "incoming", "complete", "", True, True),
            ("disabled", "incoming", "complete", "", True, True),
            ("no-receive", "incoming", "complete", "", True, True),
            ("no-token", "incoming", "complete", "", True, True),
            ("legacy", "incoming", "complete", "", True, True),
            ("missing", "incoming", "complete", "", True, True),
            ("", "incoming", "complete", "", True, True),
            ("ready", "incoming", "prepared", "", False, False),
            ("ready", "outgoing", "complete", "", False, False),
            ("ready", "incoming-pull", "complete", "", False, False),
            ("ready", "incoming", "complete", "delegated-fixture", False, False),
        )
        receipts = {}
        for seq, (source, direction, status, capability, note, link) in enumerate(states, 1):
            digest = f"{seq:012x}" + "0" * 52
            with app.app_context():
                store.create_transfer(
                    f"fixture-{seq}", direction=direction, status=status, source_peer=source,
                    operation="COPY", blob_hash=digest, capability=capability,
                )
            receipts[digest[:12]] = (note, link)

        catalog = FederationCatalog(self.root)
        for peer, name in (("ready", "shared-source.txt"), ("other", "unrelated-source.txt")):
            catalog.begin_index(peer, "one")
            catalog.ingest(peer, "one", [{"document_id": name, "path": name, "blob_hash": "f" * 64, "size": 3}])
            catalog.finish_index(peer, "one")

        before = store.list_transfers(), catalog.queue()
        for language, hint, label in (
            ("de", "hat keine Datei in „Dokumente“ importiert", "Dateikataloge ansehen"),
            ("en", "did not import a file into Documents", "Browse file catalogues"),
        ):
            with self.client.session_transaction() as session:
                session["simpleoffice_language"] = language
            response = self.client.get(self.transfers)
            self.assertEqual(200, response.status_code)
            page = BeautifulSoup(response.data, "html.parser")
            for digest_prefix, (note, link) in receipts.items():
                with self.subTest(language=language, digest_prefix=digest_prefix):
                    row = page.find("code", string=f"{digest_prefix}…").find_parent("tr")
                    self.assertEqual(note, hint in row.get_text())
                    anchors = row.select("a[href]")
                    self.assertEqual(int(link), len(anchors))
                    if not link:
                        continue
                    target = urlsplit(anchors[0]["href"])
                    self.assertEqual(("", "", "/admin/federation", "remote-files"),
                                     (target.scheme, target.netloc, target.path, target.fragment))
                    self.assertEqual({"view": ["files"]}, parse_qs(target.query))
                    self.assertEqual(label, anchors[0].get_text(strip=True))
                    view = self.client.get(f"{target.path}?{target.query}")
                    self.assertEqual(200, view.status_code)
                    filtered = BeautifulSoup(view.data, "html.parser").select_one("#remote-files")
                    self.assertIn("shared-source.txt", filtered.get_text())
                    self.assertIn("unrelated-source.txt", filtered.get_text())
        self.assertEqual(before, (store.list_transfers(), catalog.queue()))

        outsider = app.test_client()
        outsider.post("/auth/login", data={
            "username": "federation-ui-user", "password": "federation-user-password",
        })
        for destination in (self.transfers, "/admin/federation?view=files&remote_peer=ready"):
            self.assertEqual(403, outsider.get(destination).status_code)

    def test_completed_receipt_has_no_catalogue_link_without_local_receive_setup(self):
        store = FederationStore(self.root)
        for name, enabled, token, receive in (
            ("disabled", False, "fixture-credential", True),
            ("no-token", True, "", True),
            ("no-receive", True, "fixture-credential", False),
            ("legacy", True, "fixture-credential", None),
        ):
            with app.app_context():
                store.save_peer(
                    name, name, "https://federation-fixture.example.test", token,
                    enabled=enabled, policy={"documents": {"receive": receive}} if receive is not None else {},
                )
        store.create_transfer("fixture", direction="incoming", status="complete", operation="COPY", blob_hash="f" * 64)
        page = BeautifulSoup(self.client.get(self.transfers).data, "html.parser")
        row = page.find("code", string="ffffffffffff…").find_parent("tr")
        self.assertIn("hat keine Datei in „Dokumente“ importiert", row.get_text())
        self.assertIsNone(row.find("a"))
