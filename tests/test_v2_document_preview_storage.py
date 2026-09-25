import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database
from app.document_store import DocumentStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents


class V2DocumentPreviewStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.previous = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        app.config.update(
            TESTING=True,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "jens", "password": "browser-passwort"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "jens", "password": "browser-passwort"},
        )

        path = self.root / "inbox" / "preview.txt"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"authoritative preview")
        self.store = DocumentStore(self.root)
        self.store.scan()
        self.document = self.store.get_document(path)

        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(
            self.root,
            apply=True,
            acknowledge_local_plaintext=True,
        )
        activate_v2(
            self.root,
            apply=True,
            acknowledge_local_plaintext=True,
        )
        path.unlink()

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_preview_uses_verified_storage_when_plaintext_projection_is_missing(self):
        response = self.client.get(
            f"/documents/{self.document['document_id']}/preview"
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(b"authoritative preview", response.data)
        self.assertEqual("private, max-age=0, no-cache", response.headers["Cache-Control"])
        self.assertEqual(str(len(b"authoritative preview")), response.headers["Content-Length"])
        self.assertTrue(response.headers.get("ETag"))

    def test_thumbnail_fallback_uses_verified_storage_not_missing_plaintext_file(self):
        response = self.client.get(
            f"/documents/{self.document['document_id']}/thumbnail"
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(b"authoritative preview", response.data)


if __name__ == "__main__":
    unittest.main()
