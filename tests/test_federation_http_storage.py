"""Legacy HTTP downloads own verified StoragePort bytes and stream lifetimes."""
import hashlib
import io
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.document_store import DocumentStore
from app.federation_core import DEFAULT_CHUNK_SIZE
from app.federation_http import BLOCK_SIZE, bp
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.materialize import materialize_verified_object
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


class FederationHttpStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="synthetic-http-context")
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer synthetic-http-peer"}
        environment = patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_TOKEN": "synthetic-http-peer"})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(lambda: clear_runtime_storage_master_key(self.root))
        self.content = b"0123456789abcdef" * (BLOCK_SIZE // 16 + 10)
        with self.app.app_context():
            self.store = DocumentStore(self.root)
            self.document = self.store.import_upload(io.BytesIO(self.content), "source.bin", "tester")
        self.projection = self.root / self.document["last_path"]
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.doc_url = f'/federation/v1/documents/{self.document["document_id"]}'
        self.blob_url = f"/federation/v1/blobs/{self.digest}"
        self.paths = []
        for module in ("app.federation_http", "app.federation_worker"):
            materializer = patch(module + ".materialize_verified_object", side_effect=self.materialize)
            materializer.start()
            self.addCleanup(materializer.stop)

    @contextmanager
    def materialize(self, *args, **kwargs):
        with materialize_verified_object(*args, **kwargs) as path:
            self.paths.append(path)
            self.assertNotEqual(self.projection, path)
            yield path

    def migrate(self):
        with self.app.app_context():
            backup = self.base / "backup"
            create_migration_backup(self.root, backup)
            transfer_legacy_documents(self.root, backup)
            prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
            activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def assert_clean(self):
        self.assertTrue(all(not p.exists() and not p.parent.exists() for p in self.paths))

    def get(self, url, status=200, **kwargs):
        response = self.client.get(url, headers=self.auth, **kwargs)
        self.addCleanup(response.close)
        self.assertEqual(status, response.status_code)
        return response

    def assert_routes(self):
        for url in (self.doc_url + "/blob", self.blob_url):
            response = self.get(url)
            self.assertEqual(self.content, response.data)
            self.assertEqual(self.digest, response.headers["X-Content-SHA256"])
            self.assertEqual(f'"sha256:{self.digest}"', response.headers["ETag"])
            self.assert_clean()
        response = self.get(self.doc_url + "/manifest")
        self.assertEqual("sha256:" + self.digest, response.json["blob_hash"])
        self.assertEqual(len(self.content), response.json["size"])
        manifest = self.get(self.blob_url + "/manifest").json
        self.assertEqual(self.digest, manifest["blob_hash"])
        self.assertEqual(len(self.content), manifest["size"])
        self.assertEqual(hashlib.sha256(self.content[:DEFAULT_CHUNK_SIZE]).hexdigest(), manifest["chunks"][0]["hash"])
        response = self.get(self.blob_url + "/chunks/0", 206)
        self.assertEqual(self.content[:DEFAULT_CHUNK_SIZE], response.data)
        available = self.get(self.blob_url + "/availability").json
        self.assertEqual(self.digest, available["blob_hash"])
        self.assertEqual(manifest["chunk_count"], available["chunk_count"])
        self.assert_clean()

    def test_v1_routes(self):
        self.assert_routes()

    def test_v2_routes_without_projection(self):
        self.migrate()
        self.projection.unlink()
        self.assert_routes()

    def test_v2_routes_ignore_stale_projection(self):
        self.migrate()
        self.projection.write_bytes(b"stale")
        self.assert_routes()

    def test_encrypted_v2_routes_without_projection(self):
        self.migrate()
        with self.app.app_context():
            profiles = MasterKeyProfileStore(self.root, "synthetic-http-setup")
            profiles.create(STORAGE_PROFILE_ID, "synthetic-http-unlock")
            key = profiles.unlock_with_password(STORAGE_PROFILE_ID, "synthetic-http-unlock")
            self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password = self.base / "unlock.txt"
        password.write_text("synthetic-http-unlock")
        password.chmod(0o600)
        self.projection.unlink()
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password)}):
            self.assert_routes()

    def test_range_suffix_head_conditional_and_bad_range_cleanup(self):
        self.migrate()
        self.projection.unlink()
        for url in (self.doc_url + "/blob", self.blob_url):
            for header, content in (("bytes=4-9", self.content[4:10]), ("bytes=-4", self.content[-4:])):
                response = self.client.get(url, headers={**self.auth, "Range": header})
                self.addCleanup(response.close)
                self.assertEqual(206, response.status_code)
                self.assertEqual(content, response.data)
                self.assert_clean()
            head = self.client.head(url, headers=self.auth)
            self.assertEqual(200, head.status_code)
            self.assertEqual(str(len(self.content)), head.headers["Content-Length"])
            self.assertEqual(b"", head.data)
            self.assert_clean()
            cached = self.client.get(url, headers={**self.auth, "If-None-Match": f'"sha256:{self.digest}"'})
            self.assertEqual(304, cached.status_code)
            self.assert_clean()
            invalid = self.client.get(url, headers={**self.auth, "Range": "bytes=999999999-"})
            self.assertEqual(416, invalid.status_code)
            self.assert_clean()
        self.get(self.blob_url + "/chunks/999999", 404)
        self.assert_clean()

    def test_stream_keeps_materialization_until_exhaustion(self):
        self.migrate()
        self.projection.unlink()
        response = self.get(self.blob_url, buffered=False)
        self.assertTrue(self.paths[-1].exists())
        self.assertEqual(self.content, response.data)
        self.assert_clean()

    def test_early_stream_close_removes_materialization(self):
        response = self.get(self.doc_url + "/blob", buffered=False)
        self.assertTrue(self.paths[-1].exists())
        self.assertEqual(self.content[:BLOCK_SIZE], next(iter(response.response)))
        response.close()
        self.assert_clean()

    def test_unstarted_response_close_removes_materialization(self):
        with self.app.test_request_context(self.blob_url, headers=self.auth):
            response = self.app.full_dispatch_request()
            self.assertTrue(self.paths[-1].exists())
            response.close()
            self.assert_clean()

    def test_stream_read_failure_removes_materialization(self):
        original = Path.open
        reads = []
        def opened(path, *args, **kwargs):
            if path in self.paths and args and args[0] == "rb":
                reads.append(path)
                if len(reads) > 1:
                    raise OSError("synthetic response read failure")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", opened):
            with self.assertRaises(OSError):
                self.client.get(self.blob_url, headers=self.auth)
        self.assertTrue(self.paths)
        self.assert_clean()

    def test_empty_object_has_no_chunks_and_cleans_up(self):
        with self.app.app_context():
            empty = self.store.import_upload(io.BytesIO(b""), "empty.bin", "tester")
        url = "/federation/v1/blobs/" + empty["sha256"]
        response = self.get(url)
        self.assertEqual(b"", response.data)
        self.assertEqual("0", response.headers["Content-Length"])
        self.assertEqual(0, self.get(url + "/manifest").json["chunk_count"])
        self.assertEqual([], self.get(url + "/availability").json["available"])
        self.get(url + "/chunks/0", 404)
        self.assert_clean()

    def test_corrupt_v1_never_returns_success_or_cached_response(self):
        self.projection.write_bytes(b"corrupt")
        self.assert_sources_rejected()

    def test_corrupt_v2_never_falls_back_to_valid_projection(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        self.assert_sources_rejected()

    def assert_sources_rejected(self):
        for url in (self.doc_url + "/blob", self.doc_url + "/manifest", self.blob_url,
                    self.blob_url + "/manifest", self.blob_url + "/availability", self.blob_url + "/chunks/0"):
            for method, headers in (("GET", {}), ("HEAD", {}), ("GET", {"Range": "bytes=0-1"}),
                                    ("GET", {"If-None-Match": f'"sha256:{self.digest}"'})):
                with self.subTest(url=url, method=method, headers=headers):
                    response = self.client.open(url, method=method, headers={**self.auth, **headers})
                    self.assertEqual(503, response.status_code)
                    self.assertNotIn("X-Content-SHA256", response.headers)
                    self.assertNotIn(self.content[:20], response.data)
                    response.close()
                    self.assert_clean()

    def test_missing_document_index_and_invalid_hash(self):
        self.get("/federation/v1/documents/unknown/blob", 404)
        self.get("/federation/v1/blobs/invalid", 404)
        with self.store._db() as db:
            db.execute("DELETE FROM scan_file")
        self.get(self.blob_url, 404)
        self.assert_clean()

    def test_v2_stale_scan_hash_is_ignored(self):
        self.migrate()
        wrong = hashlib.sha256(b"wrong").hexdigest()
        with self.store._db() as db:
            db.execute("UPDATE scan_file SET sha256=?", (wrong,))
        self.assert_routes()
        self.get("/federation/v1/blobs/" + wrong, 404)
        self.assert_clean()

    def test_v2_legacy_tombstone_hides_document_and_blob_routes(self):
        self.migrate()
        with self.app.app_context():
            self.store.soft_delete_document(self.document["document_id"], "tester")
        for url in (self.doc_url + "/blob", self.doc_url + "/manifest", self.blob_url,
                    self.blob_url + "/manifest", self.blob_url + "/availability", self.blob_url + "/chunks/0"):
            self.get(url, 404)
        self.assert_clean()

    def test_v2_blob_routes_without_scan_rows(self):
        self.migrate()
        self.projection.unlink()
        with self.store._db() as db:
            db.execute("DELETE FROM scan_file")
        self.assert_routes()

    def test_v2_blob_lookup_without_legacy_metadata_record(self):
        self.migrate()
        self.projection.unlink()
        with self.store._db() as db:
            db.execute("DELETE FROM scan_file")
        (self.store.documents / (self.document["document_id"] + ".json")).unlink()
        for suffix in ("", "/manifest", "/availability", "/chunks/0"):
            response = self.get(self.blob_url + suffix, 206 if suffix == "/chunks/0" else 200)
            if suffix in ("", "/chunks/0"):
                self.assertEqual(self.content, response.data)
            response.close()
        self.assert_clean()

    def test_authentication_happens_before_materialization(self):
        with patch("app.federation_http._document_source") as document, patch("app.federation_http._blob_source") as blob:
            for url in (self.doc_url + "/blob", self.doc_url + "/manifest", self.blob_url,
                        self.blob_url + "/manifest", self.blob_url + "/availability", self.blob_url + "/chunks/0"):
                self.assertEqual(401, self.client.get(url).status_code)
            document.assert_not_called()
            blob.assert_not_called()

    def test_response_construction_failure_cleans_up(self):
        with patch("app.federation_http._send", side_effect=OSError("synthetic construction failure")):
            self.get(self.blob_url, 503)
        self.assertTrue(self.paths)
        self.assert_clean()
