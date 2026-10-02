"""CRM mail previews must consume verified storage, with document permissions."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, db as database
from app.contact_extensions import _eml_preview
from app.document_store import DocumentStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.materialize import materialize_verified_object


MAIL = b"Subject: Verified mail\r\nFrom: sender@example.test\r\n\r\nOriginal body\r\n"


class CrmEmlStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.root.mkdir()
        saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('admin','unused',1)")
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('limited','unused',0)")
            db.execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) "
                       "SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='limited'")
            db.commit()
            self.admin_id = db.execute("SELECT id FROM user WHERE username='admin'").fetchone()["id"]
            self.limited_id = db.execute("SELECT id FROM user WHERE username='limited'").fetchone()["id"]
        self.document = DocumentStore(self.root).import_upload(io.BytesIO(MAIL), "mail.eml", "admin")
        self.path = self.root / self.document["last_path"]
        self.client = app.test_client()
        self.login(self.admin_id)

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def activate(self):
        backup = Path(self.temp.name) / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def routes(self):
        return [f"/documents/{self.document['document_id']}/{suffix}"
                for suffix in ("eml-preview", "eml-metadata.json")]

    def test_legacy_preview_and_metadata_remain_compatible_and_private(self):
        preview = _eml_preview(self.root, self.document["document_id"], "admin")
        self.assertEqual("Verified mail", preview["subject"])
        self.assertIn("Original body", preview["text"])
        for route in self.routes():
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(200, response.status_code)
                self.assertIn(b"Verified mail", response.data)
                self.assertEqual("private, no-store", response.headers["Cache-Control"])

    def test_v2_reads_blob_when_legacy_projection_is_missing_or_tampered(self):
        self.activate()
        self.path.unlink()
        for route in self.routes():
            self.assertEqual(200, self.client.get(route).status_code)
        self.path.write_bytes(b"Subject: Tampered projection\r\n\r\nUntrusted body")
        preview = _eml_preview(self.root, self.document["document_id"], "admin")
        self.assertEqual("Verified mail", preview["subject"])
        self.assertIn("Original body", preview["text"])
        self.assertNotIn("Untrusted", preview["text"])

    def test_corrupt_blob_fails_without_fallback_to_intact_projection(self):
        self.activate()
        chunk = next(BlobStore(self.root).chunks.iterdir())
        chunk.write_bytes(b"corrupted")
        with patch("app.contact_extensions.BytesParser") as parser:
            for route in self.routes():
                self.assertEqual(404, self.client.get(route).status_code)
            parser.assert_not_called()
        self.assertEqual(MAIL, self.path.read_bytes())

    def test_corrupt_legacy_source_is_rejected_before_parsing(self):
        self.path.write_bytes(b"Subject: Tampered\r\n\r\nBad body")
        for route in self.routes():
            self.assertEqual(404, self.client.get(route).status_code)

    def test_temporary_content_is_removed_after_success_and_parser_failure(self):
        from contextlib import contextmanager

        paths = []

        @contextmanager
        def track(*args, **kwargs):
            with materialize_verified_object(*args, **kwargs) as path:
                paths.append(path)
                self.assertEqual(MAIL, path.read_bytes())
                self.assertNotIn(self.root, path.parents)
                yield path

        with patch("app.contact_extensions.materialize_verified_object", side_effect=track):
            self.assertEqual(200, self.client.get(self.routes()[0]).status_code)
            with patch("app.contact_extensions.BytesParser") as parser:
                parser.return_value.parse.side_effect = ValueError("synthetic parser failure")
                for route in self.routes():
                    self.assertEqual(404, self.client.get(route).status_code)
        self.assertEqual(3, len(paths))
        for path in paths:
            self.assertFalse(path.parent.exists())
        self.assertEqual(MAIL, self.path.read_bytes())

    def test_document_permission_required_even_with_contact_permission(self):
        self.login(self.limited_id)
        with patch("app.contact_extensions.materialize_verified_object") as materialize:
            for route in self.routes():
                self.assertEqual(403, self.client.get(route).status_code)
            materialize.assert_not_called()

    def test_anonymous_requests_cannot_read_mail(self):
        with self.client.session_transaction() as session:
            session.clear()
        for route in self.routes():
            response = self.client.get(route)
            self.assertEqual(302, response.status_code)
            self.assertIn("/auth/login", response.headers["Location"])

    def test_wrong_format_and_unknown_document_are_rejected(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(MAIL), "mail.txt", "admin")
        with self.assertRaises(ValueError):
            _eml_preview(self.root, document["document_id"], "admin")
        for suffix in ("eml-preview", "eml-metadata.json"):
            self.assertEqual(404, self.client.get(f"/documents/unknown/{suffix}").status_code)


if __name__ == "__main__":
    unittest.main()
