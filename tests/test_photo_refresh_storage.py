"""Photo refresh parses only verified authoritative content and cleans up."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import app, db
from app.document_store import DocumentStore
from app.photo_upload import extract_photo_metadata
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


class PhotoRefreshStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(lambda: app.config.update(saved))
        self.addCleanup(lambda: clear_runtime_storage_master_key(self.root))
        app.config.update(TESTING=True, DATABASE=str(self.base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            db.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "synthetic-photo-password"})
        self.client.post("/auth/login", data={"username": "jens", "password": "synthetic-photo-password"})
        image = io.BytesIO()
        Image.new("RGB", (80, 40), "white").save(image, format="JPEG")
        self.content = image.getvalue()
        self.store = DocumentStore(self.root)
        self.document = self.store.import_upload(io.BytesIO(self.content), "photo.jpg", "jens")
        self.document_id = self.document["document_id"]
        self.path = self.root / self.document["last_path"]
        self.route = f"/documents/photos/{self.document_id}/refresh-metadata"

    def migrate(self):
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def assert_refresh(self):
        seen = []
        def inspect(path):
            seen.append(path)
            self.assertNotEqual(self.path, path)
            self.assertEqual(self.content, path.read_bytes())
            if os.name == "posix":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
            return extract_photo_metadata(path)
        with patch("app.photo_upload.extract_photo_metadata", side_effect=inspect):
            self.assertEqual(302, self.client.post(self.route).status_code)
        self.assertEqual(1, len(seen))
        self.assertFalse(seen[0].exists())
        rich = self.store.get_document(self.document_id)["attributes"]["photo_metadata"]
        self.assertEqual(80, rich["derived"]["width"])
        self.assertEqual(40, rich["derived"]["height"])

    def assert_rejected(self):
        before = self.store.get_document(self.document_id)
        with patch("app.photo_upload.extract_photo_metadata") as inspect:
            self.assertEqual(302, self.client.post(self.route).status_code)
        inspect.assert_not_called()
        self.assertEqual(before, self.store.get_document(self.document_id))

    def test_v1_verified_refresh_preserves_original(self):
        self.assert_refresh()
        self.assertEqual(self.content, self.path.read_bytes())

    def test_v2_missing_projection(self):
        self.migrate()
        self.path.unlink()
        self.assert_refresh()
        self.assertFalse(self.path.exists())

    def test_v2_stale_projection(self):
        self.migrate()
        self.path.write_bytes(b"stale projection")
        self.assert_refresh()
        self.assertEqual(b"stale projection", self.path.read_bytes())

    def test_corrupt_v2_never_falls_back_or_updates_metadata(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        self.assert_rejected()

    def test_tampered_v1_is_rejected(self):
        self.path.write_bytes(b"corrupt")
        self.assert_rejected()

    def test_missing_v1_is_rejected(self):
        self.path.unlink()
        self.assert_rejected()

    def test_verified_non_image_is_rejected_before_metadata_parser(self):
        document = self.store.import_upload(io.BytesIO(b"not an image"), "invalid.jpg", "jens")
        with patch("app.photo_upload.extract_photo_metadata") as inspect:
            self.assertEqual(302, self.client.post(f"/documents/photos/{document['document_id']}/refresh-metadata").status_code)
        inspect.assert_not_called()
        self.assertNotIn("photo_metadata", self.store.get_document(document["document_id"]).get("attributes", {}))

    def test_unsupported_extension_is_rejected_before_materialization(self):
        document = self.store.import_upload(io.BytesIO(b"plain text"), "plain.txt", "jens")
        with patch("app.photo_upload.materialize_verified_object") as materialize:
            self.assertEqual(302, self.client.post(f"/documents/photos/{document['document_id']}/refresh-metadata").status_code)
        materialize.assert_not_called()

    def test_parser_failure_cleans_up_and_does_not_update_metadata(self):
        before = self.store.get_document(self.document_id)
        seen = []
        def fail(path):
            seen.append(path)
            raise ValueError("synthetic parser failure")
        with patch("app.photo_upload.extract_photo_metadata", side_effect=fail):
            self.assertEqual(302, self.client.post(self.route).status_code)
        self.assertEqual(1, len(seen))
        self.assertFalse(seen[0].exists())
        self.assertEqual(before, self.store.get_document(self.document_id))
        with self.client.session_transaction() as session:
            self.assertNotIn("synthetic parser failure", str(session.get("_flashes")))

    def test_feature_denial_prevents_materialization(self):
        with app.app_context():
            conn = db.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='jens'")
            conn.execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='jens'")
            conn.commit()
        with patch("app.photo_upload.materialize_verified_object") as materialize:
            self.assertEqual(403, self.client.post(self.route).status_code)
        materialize.assert_not_called()

    def test_anonymous_refresh_is_denied(self):
        with patch("app.photo_upload.materialize_verified_object") as materialize:
            self.assertEqual(302, app.test_client().post(self.route).status_code)
        materialize.assert_not_called()

    def test_encrypted_v2_without_plaintext_sources(self):
        self.migrate()
        phrase = "synthetic-photo-storage-unlock"
        profile = MasterKeyProfileStore(self.root, "synthetic-setup")
        profile.create(STORAGE_PROFILE_ID, phrase)
        key = profile.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password_file = self.base / "unlock.txt"
        password_file.write_text(phrase, encoding="utf-8")
        password_file.chmod(0o600)
        self.path.unlink()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"plaintext unavailable")
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password_file)}):
            self.assert_refresh()
