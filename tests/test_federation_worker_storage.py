"""Outgoing legacy transfers must use verified authoritative document bytes."""
import io
import os
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.document_store import DocumentStore
from app.federation_core import bitmap_encode, build_manifest
from app.federation_store import FederationStore
from app.federation_worker import _find_blob, push_blob_to_peer, push_blob_to_transient_target
from app.v2.blob_store import BlobStore
from app.v2.catalog import ObjectCatalog
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.materialize import materialize_verified_object
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


class FederationWorkerStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        app = Flask(__name__)
        app.config.update(SECRET_KEY="synthetic-worker-context", DOCUMENT_ROOT=str(self.root))
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.addCleanup(lambda: clear_runtime_storage_master_key(self.root))
        self.content = b"authoritative-federation-transfer"
        self.documents = DocumentStore(self.root)
        self.document = self.documents.import_upload(io.BytesIO(self.content), "sample.bin", "tester")
        self.projection = self.root / self.document["last_path"]
        self.manifest = build_manifest(self.projection, chunk_size=8)
        self.store = FederationStore(self.root)
        self.store.save_peer("target", "Target", "https://peer.example.test", "synthetic-peer-token")
        self.store.create_transfer(
            "send-one", direction="outgoing", operation="COPY", blob_hash=self.document["sha256"],
            target_peer="target", target_url="https://peer.example.test",
            capability="synthetic-transfer-capability", manifest=self.manifest,
            total_bytes=len(self.content), total_chunks=self.manifest["chunk_count"],
        )
        self.paths = []
        self.uploads = []

    def migrate(self):
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    @contextmanager
    def materialize(self, *args, **kwargs):
        with materialize_verified_object(*args, **kwargs) as path:
            self.paths.append(path)
            self.assertNotEqual(self.projection, path)
            if os.name == "posix":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
            yield path

    def response(self, url, **kwargs):
        self.assertTrue(self.paths[-1].exists())
        self.uploads.append((url, kwargs))
        response = io.BytesIO(b'{}')
        response.status = 200
        return response

    def send(self, *, delegated=False, have=(), failure=None):
        remote = {"have_bitmap": bitmap_encode(set(have), self.manifest["chunk_count"])}
        complete = {"status": "verified", "transferred_bytes": len(self.content)}
        statuses = [remote, complete] if delegated else [complete]
        with patch("app.federation_worker.materialize_verified_object", side_effect=self.materialize), \
                patch("app.federation_worker._prepare_remote", return_value=remote) as prepare, \
                patch("app.federation_worker._remote_status", side_effect=statuses), \
                patch("app.federation_worker.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.20.30.40", 443))]), \
                patch("app.federation_worker._request", side_effect=failure or self.response):
            call = push_blob_to_transient_target if delegated else push_blob_to_peer
            result = call(self.root, "send-one")
            self.assertEqual(0 if delegated else 1, prepare.call_count)
            return result

    def assert_success(self, *, delegated=False, have=()):
        self.assertEqual("complete", self.send(delegated=delegated, have=have)["status"])
        expected = [c for c in self.manifest["chunks"] if c["index"] not in have]
        self.assertEqual(len(expected), len(self.uploads))
        for chunk, (url, kwargs) in zip(expected, self.uploads):
            self.assertTrue(url.endswith(f'/chunks/{chunk["index"]}'))
            self.assertEqual(self.content[chunk["offset"]:chunk["offset"] + chunk["length"]], kwargs["body"])
            self.assertEqual("synthetic-transfer-capability" if delegated else "synthetic-peer-token", kwargs["token"])
        self.assertTrue(self.paths)
        self.assertTrue(all(not path.exists() and not path.parent.exists() for path in self.paths))

    def assert_source_failure(self, exception):
        complete = {"status": "verified", "have_bitmap": bitmap_encode(set(range(self.manifest["chunk_count"])), self.manifest["chunk_count"])}
        with patch("app.federation_worker._prepare_remote", return_value=complete) as prepare, \
                patch("app.federation_worker._remote_status", return_value=complete) as status, \
                patch("app.federation_worker._request") as request:
            with self.assertRaises(exception):
                push_blob_to_peer(self.root, "send-one")
        prepare.assert_not_called()
        status.assert_not_called()
        request.assert_not_called()
        self.assertEqual("failed", self.store.get_transfer("send-one")["status"])

    def test_v1_direct_transfer(self):
        self.assert_success()

    def test_compatibility_blob_lookup_handles_sqlite_tuple_rows(self):
        self.assertEqual(self.projection, _find_blob(self.root, self.document["sha256"]))

    def test_missing_index_entry_records_failure(self):
        with self.documents._db() as db:
            db.execute("DELETE FROM scan_file")
        self.assert_source_failure(ValueError)

    def test_v2_direct_transfer_without_projection(self):
        self.migrate()
        self.projection.unlink()
        self.assert_success()

    def test_v2_blob_lookup_uses_catalog_without_legacy_scan_index(self):
        self.migrate()
        self.projection.unlink()
        with self.documents._db() as db:
            db.execute("DELETE FROM scan_file")
        self.assert_success()

    def test_v2_missing_catalog_match_does_not_fall_back_to_scan_index(self):
        self.migrate()
        with ObjectCatalog(self.root)._db() as db:
            db.execute(
                "UPDATE object_catalog SET content_sha256=? WHERE object_id=?",
                ("a" * 64, self.document["document_id"]),
            )
        self.assert_source_failure(ValueError)

    def test_v2_stale_projection_is_not_uploaded(self):
        self.migrate()
        self.projection.write_bytes(b"stale projection")
        self.assert_success()

    def test_delegated_resume_preserves_capability_and_skips_received_chunks(self):
        self.migrate()
        self.projection.unlink()
        self.assert_success(delegated=True, have=(0, 2))

    def test_corrupt_v2_fails_before_network_even_if_target_has_all_chunks(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        self.assert_source_failure(RuntimeError)

    def test_missing_v2_does_not_fall_back_to_projection(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.unlink()
        self.assert_source_failure((RuntimeError, FileNotFoundError))

    def test_corrupt_v1_is_rejected(self):
        self.projection.write_bytes(b"corrupt")
        self.assert_source_failure(RuntimeError)

    def test_missing_v1_is_rejected(self):
        self.projection.unlink()
        self.assert_source_failure(FileNotFoundError)

    def test_stale_index_cannot_send_new_version_under_old_blob_hash(self):
        self.migrate()
        from app.v2.storage_runtime import replace_document
        replace_document(self.root, "tester", self.document["document_id"], b"new version")
        with self.documents._db() as db:
            db.execute("UPDATE scan_file SET sha256=?", (self.document["sha256"],))
        self.assert_source_failure(ValueError)

    def test_network_failure_cleans_up_and_can_be_retried(self):
        with self.assertRaises(OSError):
            self.send(failure=OSError("synthetic network failure"))
        self.assertEqual("failed", self.store.get_transfer("send-one")["status"])
        self.assertTrue(all(not path.exists() for path in self.paths))
        self.assert_success()

    def test_prepare_failure_cleans_up_and_records_failure_without_payload(self):
        failure = urllib.error.HTTPError("https://peer.example.test", 403, "Forbidden", {}, io.BytesIO(b"private response"))
        self.addCleanup(failure.close)
        with patch("app.federation_worker.materialize_verified_object", side_effect=self.materialize), \
                patch("app.federation_worker._prepare_remote", side_effect=failure):
            with self.assertRaises(urllib.error.HTTPError):
                push_blob_to_peer(self.root, "send-one")
        transfer = self.store.get_transfer("send-one")
        self.assertEqual("failed", transfer["status"])
        self.assertEqual("HTTP 403", transfer["error"])
        self.assertFalse(self.paths[-1].exists())

    def test_encrypted_v2_without_plaintext_sources(self):
        self.migrate()
        phrase = "synthetic-federation-unlock"
        profiles = MasterKeyProfileStore(self.root, "synthetic-setup")
        profiles.create(STORAGE_PROFILE_ID, phrase)
        key = profiles.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password_file = self.base / "unlock.txt"
        password_file.write_text(phrase)
        password_file.chmod(0o600)
        self.projection.unlink()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"plaintext unavailable")
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password_file)}):
            self.assert_success()
