"""Exercise the existing player source with real document/storage metadata."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database, get_db
from app.document_store import DocumentStore
from app.v2.contracts import LogicalObjectId, OperationResult, StorageLocation
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import storage_for


class VideoPreviewHttpTests(unittest.TestCase):
    mode = "v1"
    payload = b"0123456789"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(app.config.update, previous)
        app.config.update(TESTING=True, DATABASE=str(self.base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "tester", "password": "test-video-password"})
        self.client.post("/auth/login", data={"username": "tester", "password": "test-video-password"})
        self.store = DocumentStore(self.root)
        self.store.initialize()
        self.path = self.root / "preview.mp4"
        self.path.write_bytes(self.payload)
        self.store._scan_file(self.path, force_hash=True)
        self.document = self.store.get_document(self.path)
        self.assertNotIn("size", self.document)
        self.object_id = LogicalObjectId(self.document["document_id"])
        self.url = f"/documents/{self.object_id.value}/preview?raw=1"
        if self.mode != "v1":
            backup = self.base / "backup"
            create_migration_backup(self.root, backup)
            transfer_legacy_documents(self.root, backup)
            prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        if self.mode in {"v2", "encrypted"}:
            activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)
            if self.mode == "encrypted":
                import secrets
                master_key = secrets.token_bytes(32)
                report = encrypted_blob_cutover(self.root, master_key, apply=True)
                self.assertTrue(report["ready"])
                key_patch = patch("app.v2.storage_runtime.runtime_storage_master_key", return_value=master_key)
                key_patch.start()
                self.addCleanup(key_patch.stop)
            self.path.unlink()

    def _request(self, *, method="GET", headers=None):
        response = self.client.open(self.url, method=method, headers=headers or {})
        self.addCleanup(response.close)
        return response

    def test_bounded_open_suffix_and_clamped_ranges(self):
        for value, expected, content_range in (
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=7-", b"789", "bytes 7-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
            ("bytes=7-100", b"789", "bytes 7-9/10"),
            ("bytes=-100", self.payload, "bytes 0-9/10"),
        ):
            with self.subTest(range=value):
                response = self._request(headers={"Range": value})
                self.assertEqual(206, response.status_code)
                self.assertEqual(expected, response.data)
                self.assertEqual(content_range, response.headers["Content-Range"])
                self.assertEqual(len(expected), response.content_length)
                self.assertEqual("video/mp4", response.mimetype)
                self.assertEqual("bytes", response.headers["Accept-Ranges"])
                self.assertEqual("private, no-cache", response.headers["Cache-Control"])
                self.assertEqual("sandbox", response.headers["Content-Security-Policy"])

    def test_invalid_ranges_report_authoritative_size(self):
        for value in ("bytes=10-", "bytes=5-2", "bytes=-0", "bytes=0-1,4-5", "items=0-1"):
            with self.subTest(range=value):
                response = self._request(headers={"Range": value})
                self.assertEqual(416, response.status_code)
                self.assertEqual("bytes */10", response.headers["Content-Range"])

    def test_head_ignores_range_and_returns_full_length_without_body(self):
        response = self._request(method="HEAD", headers={"Range": "bytes=100-"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(b"", response.data)
        self.assertEqual(len(self.payload), response.content_length)
        self.assertNotIn("Content-Range", response.headers)

    def test_if_range_only_accepts_current_strong_etag(self):
        etag = self._request().headers["ETag"]
        current = self._request(headers={"Range": "bytes=2-5", "If-Range": etag})
        self.assertEqual(206, current.status_code)
        self.assertEqual(b"2345", current.data)
        self.assertEqual(etag, current.headers["ETag"])
        for validator in ('"stale"', f"W/{etag}", "Sun, 04 Oct 2026 12:00:00 GMT"):
            with self.subTest(validator=validator):
                response = self._request(headers={"Range": "bytes=100-", "If-Range": validator})
                self.assertEqual(200, response.status_code)
                self.assertEqual(self.payload, response.data)

    def test_unauthenticated_request_cannot_read_video_range(self):
        response = app.test_client().get(self.url, headers={"Range": "bytes=2-5"})
        self.addCleanup(response.close)
        self.assertEqual(302, response.status_code)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_logged_in_user_cannot_read_another_rooms_private_video(self):
        metadata = self.store.get_document(self.object_id.value)
        metadata.setdefault("attributes", {})["chat_attachment"] = {
            "visibility": "chat", "allowed_local_users": ["another-user"],
        }
        self.store._save_document(metadata)
        with app.app_context():
            db = get_db()
            db.execute("UPDATE user SET is_admin=0 WHERE username=?", ("tester",))
            db.commit()
        response = self._request(headers={"Range": "bytes=2-5"})
        self.assertEqual(404, response.status_code)
        self.assertNotEqual(b"2345", response.data)

    def test_empty_video_reports_zero_length_without_partial_content(self):
        created = storage_for(self.root, "tester").create_bytes(StorageLocation("empty.mp4"), b"")
        self.assertTrue(created.ok, created.error)
        self.url = f"/documents/{created.value.object_id.value}/preview?raw=1"
        if self.mode in {"v2", "encrypted"}:
            (self.root / "empty.mp4").unlink()
        response = self._request(headers={"Range": "bytes=0-"})
        self.assertEqual(416, response.status_code)
        self.assertEqual("bytes */0", response.headers["Content-Range"])
        full = self._request()
        self.assertEqual(200, full.status_code)
        self.assertEqual(b"", full.data)
        self.assertEqual(0, full.content_length)

    def test_deleted_document_cannot_return_partial_content(self):
        # V2 mutations intentionally still require the compatibility projection.
        if self.mode in {"v2", "encrypted"}:
            self.path.write_bytes(self.payload)
        result = storage_for(self.root, "tester").delete(self.object_id)
        self.assertTrue(result.ok, result.error)
        response = self._request(headers={"Range": "bytes=2-5"})
        self.assertEqual(404, response.status_code)

    def test_corruption_outside_requested_range_is_not_published(self):
        if self.mode in {"v1", "shadow"}:
            self.path.write_bytes(b"012345678X")
        else:
            storage = storage_for(self.root, "tester")
            blobs = storage.primary.blobs
            entry = storage.catalog.get(self.object_id).value
            manifest = blobs.version_manifest(entry.version_id)
            chunk = blobs._chunk_path(manifest["chunks"][-1]["physical_id"])
            damaged = bytearray(chunk.read_bytes())
            damaged[-1] ^= 1
            chunk.write_bytes(damaged)
        response = self._request(headers={"Range": "bytes=2-5"})
        self.assertEqual(404, response.status_code)
        self.assertNotEqual(b"2345", response.data)

    def test_changed_version_is_not_published_even_with_same_size(self):
        storage = storage_for(self.root, "tester")
        original = storage.copy_verified_range_to

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertTrue(result.ok)
            return OperationResult.success(replace(result.value, version="different-version"))

        with patch.object(storage, "copy_verified_range_to", side_effect=changed), patch(
            "app.documents_routes_content._storage", return_value=storage,
        ):
            response = self._request(headers={"Range": "bytes=2-5"})
        self.assertEqual(404, response.status_code)
        self.assertNotEqual(b"2345", response.data)


class ShadowVideoPreviewHttpTests(VideoPreviewHttpTests):
    mode = "shadow"


class V2VideoPreviewHttpTests(VideoPreviewHttpTests):
    mode = "v2"


class EncryptedVideoPreviewHttpTests(VideoPreviewHttpTests):
    mode = "encrypted"


if __name__ == "__main__":
    unittest.main()
