"""HTTP contracts for the bounded Calibre metadata import."""
from __future__ import annotations

import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from flask import Flask, g, session

from app import db as database
from app.library.routes import bp as library_blueprint
from app.security_controls import protect_browser_mutation


class CalibreImportHttpContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DATABASE=str(root / "users.sqlite3"),
            DOCUMENT_ROOT=str(root),
            SECRET_KEY="calibre-import-test",
        )
        with self.app.app_context():
            database.ensure_auth_database()

        @self.app.before_request
        def authenticate_test_user():
            g.user = {
                "id": 1,
                "username": "admin",
                "is_admin": True,
                "is_disabled": False,
            } if session.get("username") else None

        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "login")
        self.app.before_request(protect_browser_mutation)
        self.app.register_blueprint(library_blueprint)
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["username"] = "admin"
            state["_csrf_token"] = "calibre-import-csrf"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _archive(*, title="Imported title", isbn="9780306406157"):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=(
            "title", "authors", "series", "series_index", "publisher", "published",
            "identifiers", "tags", "comments", "language",
        ))
        writer.writeheader()
        writer.writerow({
            "title": title, "authors": "Ada Author", "series": "Series",
            "series_index": "1", "publisher": "Publisher", "published": "2024",
            "identifiers": f"isbn:{isbn}", "tags": "Novel", "comments": "Description",
            "language": "de",
        })
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.csv", stream.getvalue().encode("utf-8-sig"))
            archive.writestr("opf/ignored.opf", b"not executed")
        return payload.getvalue()

    def _post(self, payload=None, csrf=True):
        data = {}
        if csrf:
            data["_csrf_token"] = "calibre-import-csrf"
        if payload is not None:
            data["file"] = (io.BytesIO(payload), "library.zip")
        return self.client.post("/library/import/books.calibre", data=data)

    def test_import_creates_book_and_duplicate_is_skipped(self):
        first = self._post(self._archive())
        second = self._post(self._archive())

        self.assertEqual(200, first.status_code)
        self.assertEqual({"ok": True, "created": 1, "skipped": 0, "total": 1}, first.get_json())
        self.assertEqual(200, second.status_code)
        self.assertEqual({"ok": True, "created": 0, "skipped": 1, "total": 1}, second.get_json())

    def test_invalid_archive_and_missing_csrf_are_rejected(self):
        invalid = self._post(b"not-a-zip")
        no_csrf = self._post(self._archive(), csrf=False)

        self.assertEqual(400, invalid.status_code)
        self.assertEqual(403, no_csrf.status_code)

    def test_unauthenticated_import_is_redirected(self):
        anonymous = self.app.test_client()
        with anonymous.session_transaction() as state:
            state["_csrf_token"] = "calibre-import-csrf"
        response = anonymous.post(
            "/library/import/books.calibre", data={"_csrf_token": "calibre-import-csrf"},
        )
        self.assertEqual(302, response.status_code)


if __name__ == "__main__":
    unittest.main()
