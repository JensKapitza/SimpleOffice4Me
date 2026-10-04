"""Scoped block responses use one verified, private StoragePort snapshot."""
import os
import unittest
from unittest.mock import patch

import test_federation_http_storage as fixture
from app.federation_blocks import FederationBlockStore, build_content_manifest, sha512_bytes
from app.federation_blocks_v2_http import bp
from app.federation_peer_auth import headers as peer_headers
from app.federation_store import FederationStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID
from app.v2.storage_runtime import replace_document


class FederationScopedStorageTests(unittest.TestCase):
    migrate = fixture.FederationHttpStorageTests.migrate
    materialize = fixture.FederationHttpStorageTests.materialize
    assert_clean = fixture.FederationHttpStorageTests.assert_clean

    def setUp(self):
        fixture.FederationHttpStorageTests.setUp(self)
        self.app.register_blueprint(bp)
        with self.app.app_context():
            FederationStore(self.root).save_peer("source", "Source", "https://source.invalid", "source-identity")
        materializer = patch("app.federation_blocks_v2_http.materialize_verified_object", side_effect=self.materialize)
        materializer.start()
        self.addCleanup(materializer.stop)
        self.manifest_url = f'/federation/v2/blocks/documents/{self.document["document_id"]}/manifest'
        self.block_url = f'/federation/v2/blocks/documents/{self.document["document_id"]}/blocks/0'

    def request(self, url, status=200, *, query=None, method="GET"):
        auth = {**self.auth, **peer_headers("source", "source-identity", method, url)}
        response = self.client.open(url, method=method, headers=auth, query_string=query)
        self.addCleanup(response.close)
        self.assertEqual(status, response.status_code, response.data)
        self.assert_clean()
        return response

    def proof(self):
        manifest = self.request(self.manifest_url).json
        return {"session": manifest["session"], "proof": manifest["blocks"][0]["token"]}

    def assert_content(self):
        manifest = self.request(self.manifest_url)
        self.assertEqual(len(self.content), manifest.json["size"])
        self.assertNotIn("sha512", manifest.get_data(as_text=True))
        self.assertNotIn(self.digest, manifest.get_data(as_text=True))
        proof = {"session": manifest.json["session"], "proof": manifest.json["blocks"][0]["token"]}
        self.assertEqual(self.content, self.request(self.block_url, query=proof).data)
        self.assertFalse((self.root / ".simpleoffice-control" / "federation-blocks.sqlite3").exists())

    def test_v1_and_shadow_sources_are_verified(self):
        self.assert_content()
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        self.assert_content()

    def test_v2_without_projection_or_scan_index(self):
        self.migrate()
        self.projection.unlink()
        with self.store._db() as db:
            db.execute("DELETE FROM scan_file")
        self.assert_content()

    def test_v2_ignores_stale_projection(self):
        self.migrate()
        self.projection.write_bytes(b"stale")
        self.assert_content()

    def test_encrypted_v2_without_projection(self):
        self.migrate()
        with self.app.app_context():
            profiles = MasterKeyProfileStore(self.root, "synthetic-scoped-setup")
            profiles.create(STORAGE_PROFILE_ID, "synthetic-scoped-unlock")
            key = profiles.unlock_with_password(STORAGE_PROFILE_ID, "synthetic-scoped-unlock")
            self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password = self.base / "unlock.txt"
        password.write_text("synthetic-scoped-unlock")
        password.chmod(0o600)
        self.projection.unlink()
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password)}):
            self.assert_content()

    def test_corrupt_authority_cannot_fall_back_to_projection_or_cached_block(self):
        self.migrate()
        proof = self.proof()
        FederationBlockStore(self.root).put_cached_block(sha512_bytes(self.content), self.content)
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        for url, query in ((self.manifest_url, None), (self.block_url, proof)):
            response = self.request(url, 503, query=query)
            self.assertEqual({"error": "storage_unavailable"}, response.json)

    def test_corrupt_v1_is_rejected_and_temp_removed(self):
        self.projection.write_bytes(b"corrupt")
        self.request(self.manifest_url, 503)
        self.request(self.block_url, 503)

    def test_deleted_document_does_not_accept_previously_issued_proof(self):
        self.migrate()
        proof = self.proof()
        with self.app.app_context():
            self.store.soft_delete_document(self.document["document_id"], "tester")
        self.request(self.manifest_url, 404)
        self.request(self.block_url, 404, query=proof)

    def test_replacement_invalidates_old_session_and_proof(self):
        self.migrate()
        proof = self.proof()
        with self.app.app_context():
            replace_document(self.root, "tester", self.document["document_id"], b"new authoritative revision")
        self.request(self.block_url, 404, query=proof)
        self.assertEqual(b"new authoritative revision", self.request(self.block_url, query=self.proof()).data)

    def test_head_bad_proof_and_manifest_failure_remove_temporary_source(self):
        proof = self.proof()
        head = self.request(self.block_url, query=proof, method="HEAD")
        self.assertEqual(str(len(self.content)), head.headers["Content-Length"])
        self.assertEqual(b"", head.data)
        self.request(self.block_url, 404, query={**proof, "proof": "wrong"})
        with patch("app.federation_blocks_v2_http.build_content_manifest", side_effect=OSError("synthetic failure")):
            self.request(self.manifest_url, 404)

    def test_unsigned_or_banned_peer_is_rejected_before_materialization(self):
        with patch("app.federation_blocks_v2_http.materialize_verified_object") as source:
            self.assertEqual(401, self.client.get(self.manifest_url, headers=self.auth).status_code)
            from app.federation_moderation import FederationModerationStore
            FederationModerationStore(self.root).ban("source", "abuse", actor="admin")
            self.request(self.manifest_url, 401)
            source.assert_not_called()

    def test_block_offsets_lengths_and_invalid_index(self):
        def smaller_blocks(path):
            return build_content_manifest(path, min_size=1024, avg_size=2048, max_size=4096)
        with patch("app.federation_blocks_v2_http.build_content_manifest", side_effect=smaller_blocks):
            manifest = self.request(self.manifest_url).json
            self.assertGreater(len(manifest["blocks"]), 1)
            for block in (manifest["blocks"][0], manifest["blocks"][-1]):
                url = self.block_url.rsplit("/", 1)[0] + "/" + str(block["index"])
                query = {"session": manifest["session"], "proof": block["token"]}
                data = self.request(url, query=query).data
                self.assertEqual(self.content[block["offset"]:block["offset"] + block["length"]], data)
            self.request(self.block_url.rsplit("/", 1)[0] + "/9999", 404, query=query)
