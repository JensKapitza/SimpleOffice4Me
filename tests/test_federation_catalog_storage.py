"""Catalog metadata follows StoragePort authority, without reading file content."""
import hashlib
import io
import os
import unittest
from unittest.mock import patch

import test_federation_http_storage as fixture
from app.document_store import DocumentStore
from app.federation_catalog_http import bp
from app.federation_moderation import FederationModerationStore
from app.federation_peer_auth import headers as peer_headers
from app.federation_store import FederationStore
from app.v2.blob_store import BlobStore
from app.v2.contracts import ErrorCode, OperationResult
from app.v2.cutover import prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID
from app.v2.storage_runtime import delete_document, move_document, replace_document


class FederationCatalogStorageTests(unittest.TestCase):
    migrate = fixture.FederationHttpStorageTests.migrate
    materialize = fixture.FederationHttpStorageTests.materialize

    def setUp(self):
        fixture.FederationHttpStorageTests.setUp(self)
        self.app.register_blueprint(bp)
        self.url = "/federation/v1/catalog/documents"

    def index(self, status=200, *, headers=None, query=None):
        response = self.client.get(self.url, headers=self.auth if headers is None else headers, query_string=query)
        self.addCleanup(response.close)
        self.assertEqual(status, response.status_code, response.data)
        return response.json

    def assert_document(self):
        result = self.index()
        self.assertEqual(1, result["total"])
        row = result["documents"][0]
        self.assertEqual(self.document["document_id"], row["document_id"])
        self.assertEqual(self.document["last_path"], row["path"])
        self.assertEqual(len(self.content), row["size"])
        self.assertEqual(self.digest, row["blob_hash"])
        return result

    def test_v1_and_shadow_keep_catalog_shape(self):
        self.assert_document()
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        self.assert_document()

    def test_v2_without_projection_or_scan_rows(self):
        self.migrate()
        self.projection.unlink()
        with self.store._db() as db:
            db.execute("DELETE FROM scan_file")
        with patch.object(BlobStore, "read", side_effect=AssertionError("catalog must not read content")):
            self.assert_document()

    def test_stale_projection_and_metadata_cannot_override_v2_identity(self):
        self.migrate()
        self.projection.write_bytes(b"stale")
        stale = {**self.document, "last_path": "obsolete.bin", "sha256": "f" * 64, "size": 1}
        with patch.object(DocumentStore, "list_documents", return_value=[stale]):
            self.assert_document()

    def test_move_and_replacement_change_generation_and_authoritative_fields(self):
        self.migrate()
        before = self.index()
        (self.root / "moved").mkdir()
        with self.app.app_context():
            moved = move_document(self.root, "tester", self.document["document_id"], "moved/renamed.bin")
            replace_document(self.root, "tester", self.document["document_id"], b"new revision")
        after = self.index()
        self.assertNotEqual(before["generation"], after["generation"])
        row = after["documents"][0]
        self.assertEqual(moved["last_path"], row["path"])
        self.assertTrue(row["path"].startswith("moved/"))
        self.assertEqual(len(b"new revision"), row["size"])
        self.assertEqual(hashlib.sha256(b"new revision").hexdigest(), row["blob_hash"])

    def test_v2_tombstone_and_authoritative_delete_are_not_advertised(self):
        self.migrate()
        with self.app.app_context():
            other = self.store.import_upload(io.BytesIO(b"other"), "other.bin", "tester")
            # Import through the active adapter to register an authoritative object.
            from app.v2.storage_runtime import import_document
            active = import_document(self.root, "tester", io.BytesIO(b"active"), "active.bin")
            self.store.soft_delete_document(self.document["document_id"], "tester")
            delete_document(self.root, "tester", active["document_id"])
        self.assertEqual(0, self.index()["total"])
        self.assertNotEqual(active["document_id"], other["document_id"])

    def test_v1_missing_or_symlink_projection_is_omitted(self):
        self.projection.unlink()
        self.assertEqual(0, self.index()["total"])
        outside = self.base / "outside.bin"
        outside.write_bytes(self.content)
        self.projection.symlink_to(outside)
        self.assertEqual(0, self.index()["total"])

    def test_encrypted_v2_catalog_and_locked_storage_failure(self):
        self.migrate()
        with self.app.app_context():
            profiles = MasterKeyProfileStore(self.root, "synthetic-catalog-setup")
            profiles.create(STORAGE_PROFILE_ID, "synthetic-catalog-unlock")
            key = profiles.unlock_with_password(STORAGE_PROFILE_ID, "synthetic-catalog-unlock")
            self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password = self.base / "unlock.txt"
        password.write_text("synthetic-catalog-unlock")
        password.chmod(0o600)
        self.projection.unlink()
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password)}):
            self.assert_document()
        from app.v2.runtime_keys import clear_runtime_storage_master_key
        clear_runtime_storage_master_key(self.root)
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: ""}):
            self.assertEqual({"error": "catalog_unavailable"}, self.index(503))

    def test_storage_failure_never_returns_successful_partial_generation(self):
        with self.app.app_context():
            self.store.import_upload(io.BytesIO(b"other"), "other.bin", "tester")
            from app.v2.storage_runtime import storage_for
            from app.v2.contracts import LogicalObjectId
            valid = storage_for(self.root, "federation-transfer").stat(LogicalObjectId(self.document["document_id"]))
        for code in (ErrorCode.STORAGE_UNAVAILABLE, ErrorCode.CONFLICT, ErrorCode.INTEGRITY_ERROR, ErrorCode.INTERNAL_ERROR):
            with self.subTest(code=code), patch("app.federation_catalog_http.storage_for") as storage:
                storage.return_value.stat.side_effect = [valid, OperationResult.failure(code, "private diagnostic")]
                self.assertEqual({"error": "catalog_unavailable"}, self.index(503))

    def test_not_found_and_forbidden_objects_are_omitted(self):
        for code in (ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.INVALID_INPUT):
            with self.subTest(code=code), patch("app.federation_catalog_http.storage_for") as storage:
                storage.return_value.stat.return_value = OperationResult.failure(code, "hidden")
                self.assertEqual(0, self.index()["total"])

    def test_tags_origins_and_pagination_survive_missing_v2_projection(self):
        self.migrate()
        with self.app.app_context():
            self.store.update_metadata(self.document["document_id"], tags=["important"], attributes={"email_origin": {"account_id": "mail"}}, author="tester")
            from app.v2.storage_runtime import import_document
            other = import_document(self.root, "tester", io.BytesIO(b"other"), "z-other.bin")
        self.projection.unlink()
        (self.root / other["last_path"]).unlink()
        first = self.index(query={"limit": 1})
        second = self.index(query={"limit": 1, "cursor": first["next_cursor"]})
        self.assertEqual(first["generation"], second["generation"])
        self.assertEqual(2, first["total"])
        self.assertIsNone(second["next_cursor"])
        row = next(row for row in first["documents"] + second["documents"] if row["document_id"] == self.document["document_id"])
        self.assertIn("important", row["tags"])
        self.assertIn("origin:email", row["origin_tags"])

    def test_authentication_and_bans_precede_storage_lookup(self):
        with patch("app.federation_catalog_http.storage_for") as storage:
            self.index(401, headers={})
            FederationModerationStore(self.root).ban("blocked", "abuse", actor="admin")
            self.index(401)
            storage.assert_not_called()
        with self.app.app_context():
            FederationStore(self.root).save_peer("allowed", "Allowed", "https://allowed.invalid", "allowed-proof")
        signed = {**self.auth, **peer_headers("allowed", "allowed-proof", "GET", self.url)}
        self.index(headers=signed)
