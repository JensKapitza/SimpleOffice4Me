"""Regression tests for whole-catalog export authorization and streaming."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.object_store import ObjectStore


class LibraryExportAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        documents = root / "documents"
        documents.mkdir()
        saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(TESTING=True, DATABASE=str(root / "users.sqlite"), DOCUMENT_ROOT=str(documents))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user (username,password,is_admin) VALUES ('admin','unused',1)")
            db.execute("INSERT INTO user (username,password,is_admin) VALUES ('normal','unused',0)")
            db.commit()
            self.admin_id = db.execute("SELECT id FROM user WHERE username='admin'").fetchone()["id"]
            self.normal_id = db.execute("SELECT id FROM user WHERE username='normal'").fetchone()["id"]
        ObjectStore(documents).create({"name": "=2+2", "type": "book", "identifier": "9780000000001"}, "admin")
        self.client = app.test_client()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def test_regular_account_cannot_download_entire_catalog(self):
        self.login(self.normal_id)
        response = self.client.get("/library/export/books.csv")
        self.assertEqual(403, response.status_code)

    def test_administrator_streams_export_with_formula_protection(self):
        self.login(self.admin_id)
        with patch.object(ObjectStore, "objects", side_effect=AssertionError("eager loading")):
            response = self.client.get("/library/export/books.csv")
            raw = response.data
        self.assertEqual(200, response.status_code)
        self.assertTrue(raw.startswith(bytes((239,187,191))))
        self.assertIn("'=2+2", response.get_data(as_text=True))
        self.assertEqual("no-store", response.headers.get("Cache-Control"))


if __name__ == "__main__":
    unittest.main()
