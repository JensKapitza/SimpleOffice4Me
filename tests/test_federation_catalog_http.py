import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app_config_store
from app.document_store import DocumentStore
from app.federation_catalog_http import bp
from app.federation_peer_auth import headers as peer_headers
from app.federation_store import FederationStore


class FederationCatalogHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="test-secret")
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        # Settings live outside DOCUMENT_ROOT in production (user-home
        # config dir); keep the fixture the same so store.scan() does not
        # index the settings file as a document.
        self.settings_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.settings_temp.cleanup)
        self._store_patcher = patch.object(
            app_config_store, "settings_path",
            return_value=Path(self.settings_temp.name) / "app-config.json",
        )
        self._store_patcher.start()
        self.addCleanup(self._store_patcher.stop)
        app_config_store.put_secret("federation", "token", "catalog-token")
        store = DocumentStore(self.root)
        first = self.root / "Alpha.txt"
        first.write_text("alpha", encoding="utf-8")
        second = self.root / "Beta.txt"
        second.write_text("beta", encoding="utf-8")
        store.scan()
        self.first = store.get_document(first)
        store.update_metadata(
            self.first["document_id"],
            tags=["important"],
            attributes={"email_origin": {"account_id": "mail-1"}},
            author="tester",
        )
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer catalog-token"}
        FederationStore(self.root).save_peer(
            "remote-peer",
            "Remote",
            "https://remote.example.test",
            "remote-peer-token",
            {"documents": {"send": True, "receive": True, "seed": True}},
        )

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def test_catalog_requires_authentication(self):
        response = self.client.get("/federation/v1/catalog/documents")
        self.assertEqual(response.status_code, 401)

    def test_catalog_accepts_peer_bound_hmac_without_legacy_bearer(self):
        path = "/federation/v1/catalog/documents"
        response = self.client.get(
            path, headers=peer_headers("remote-peer", "remote-peer-token", "GET", path, b""),
        )
        self.assertEqual(response.status_code, 200)

    def test_peer_document_policy_denies_catalog_metadata(self):
        path = "/federation/v1/catalog/documents"
        for policy in (
            {"documents": {"send": False, "receive": True, "seed": True}},
            {"documents": {"send": True, "receive": True, "seed": False}},
            {"documents": {"send": "false", "receive": True, "seed": True}},
            {"documents": None},
            [],
        ):
            with self.subTest(policy=policy):
                FederationStore(self.root).save_peer(
                    "remote-peer",
                    "Remote",
                    "https://remote.example.test",
                    "remote-peer-token",
                    policy,
                )
                self.assertEqual(
                    policy,
                    FederationStore(self.root).get_peer("remote-peer")["policy"],
                )

                response = self.client.get(
                    path,
                    headers=peer_headers(
                        "remote-peer", "remote-peer-token", "GET", path, b""
                    ),
                )

                self.assertEqual(403, response.status_code)
                self.assertEqual({"error": "catalog_policy_denied"}, response.json)
                self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_catalog_denies_identity_neutral_legacy_bearer_without_peer_scope(self):
        # Regression: a shared Bearer [REDACTED] proves no peer identity, so it must
        # never expose the document catalog, even with an explicit policy stored.
        response = self.client.get("/federation/v1/catalog/documents", headers=self.auth)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json, {"error": "catalog_policy_denied"})

    def test_catalog_denied_by_default_without_explicit_scoped_permission(self):
        # Denied by default: missing policy, missing documents section, or a
        # missing send/seed flag must not expose the catalog over peer HMAC.
        path = "/federation/v1/catalog/documents"
        denied_policies = [
            None,
            {},
            {"documents": {"send": True, "receive": True}},
            {"documents": {"seed": True, "receive": True}},
            {"chat": {"send": True, "receive": True}},
        ]
        for policy in denied_policies:
            with self.subTest(policy=policy):
                FederationStore(self.root).save_peer(
                    "remote-peer",
                    "Remote",
                    "https://remote.example.test",
                    "remote-peer-token",
                    policy,
                )
                response = self.client.get(
                    path,
                    headers=peer_headers("remote-peer", "remote-peer-token", "GET", path, b""),
                )
                self.assertEqual(403, response.status_code)
                self.assertEqual({"error": "catalog_policy_denied"}, response.json)

    def test_catalog_allows_only_explicit_scoped_document_permission(self):
        # Allowed only after explicit, scoped permission: identified peer HMAC
        # plus documents.send and documents.seed flags.
        path = "/federation/v1/catalog/documents"
        FederationStore(self.root).save_peer(
            "remote-peer",
            "Remote",
            "https://remote.example.test",
            "remote-peer-token",
            {"documents": {"send": True, "receive": True, "seed": True}},
        )
        response = self.client.get(
            path,
            headers=peer_headers("remote-peer", "remote-peer-token", "GET", path, b""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["schema"], "sofp-document-index/v1")

    def _scoped_peer_headers(self, path="/federation/v1/catalog/documents"):
        return peer_headers("remote-peer", "remote-peer-token", "GET", path, b"")

    def test_catalog_exports_documents_and_origin_tags(self):
        response = self.client.get(
            "/federation/v1/catalog/documents", headers=self._scoped_peer_headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["schema"], "sofp-document-index/v1")
        self.assertEqual(response.json["total"], 2)
        row = next(item for item in response.json["documents"] if item["document_id"] == self.first["document_id"])
        self.assertIn("important", row["tags"])
        self.assertIn("origin:email", row["origin_tags"])
        self.assertIn("source:imap", row["origin_tags"])
        self.assertEqual(len(row["blob_hash"]), 64)

    def test_catalog_pagination_is_stable_within_generation(self):
        first = self.client.get(
            "/federation/v1/catalog/documents?limit=1", headers=self._scoped_peer_headers()
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["count"], 1)
        self.assertIsNotNone(first.json["next_cursor"])
        second = self.client.get(
            f"/federation/v1/catalog/documents?limit=1&cursor={first.json['next_cursor']}",
            headers=self._scoped_peer_headers(),
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json["generation"], second.json["generation"])
        ids = {first.json["documents"][0]["document_id"], second.json["documents"][0]["document_id"]}
        self.assertEqual(len(ids), 2)

    def test_page_size_is_bounded(self):
        response = self.client.get(
            "/federation/v1/catalog/documents?limit=999999",
            headers=self._scoped_peer_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(response.json["count"], 1000)


if __name__ == "__main__":
    unittest.main()
