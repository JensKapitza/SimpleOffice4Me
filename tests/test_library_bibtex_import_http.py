"""HTTP and round-trip contracts for bounded BibTeX book import."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g, session

from app import db as database
from app.library.imports import bibtex_import_rows
from app.library.routes import bp as library_blueprint
from app.object_store import ObjectStore
from app.security_controls import protect_browser_mutation


class BibtexImportHttpContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__, template_folder="../templates", static_folder="../static")
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DATABASE=str(self.root / "users.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="bibtex-import-test",
        )
        with self.app.app_context():
            database.ensure_auth_database()

        @self.app.before_request
        def authenticate_test_user():
            g.user = {
                "id": 1,
                "username": "admin",
                "is_admin": session.get("is_admin", True),
                "is_disabled": False,
            } if session.get("username") else None

        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "login")
        self.app.before_request(protect_browser_mutation)
        self.app.register_blueprint(library_blueprint)
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["username"] = "admin"
            state["is_admin"] = True
            state["_csrf_token"] = "bibtex-import-csrf"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _bibtex(*, title="Imported title", isbn="9780306406157"):
        return f"""@book{{sample,
  title = {{{title}}},
  author = {{Ada Author and Ben Writer}},
  isbn = {{{isbn}}},
  publisher = {{Publisher}},
  year = {{2024}},
  language = {{de}},
  edition = {{2}},
  series = {{Series}},
  volume = {{3}},
  keywords = {{Novel}},
  abstract = {{Description}},
  note = {{Library location: Imported shelf}}
}}
""".encode()

    def _post(self, payload=None, *, csrf=True):
        data = {}
        if csrf:
            data["_csrf_token"] = "bibtex-import-csrf"
        if payload is not None:
            data["file"] = (io.BytesIO(payload), "books.bib")
        return self.client.post("/library/import/books.bib", data=data)

    def test_import_maps_metadata_and_skips_duplicate_isbn(self):
        first = self._post(self._bibtex())
        second = self._post(self._bibtex())

        self.assertEqual(200, first.status_code)
        self.assertEqual({"ok": True, "created": 1, "skipped": 0, "total": 1}, first.get_json())
        self.assertEqual({"ok": True, "created": 0, "skipped": 1, "total": 1}, second.get_json())
        book = ObjectStore(self.root).objects()[0]
        self.assertEqual("Ada Author; Ben Writer", book["fields"]["authors"])
        self.assertEqual("Series", book["fields"]["series"])
        self.assertEqual("Description", book["fields"]["description"])
        self.assertEqual("Library location: Imported shelf", book["fields"]["bibliography_note"])

    def test_exported_record_can_be_imported_without_losing_core_metadata(self):
        self.assertEqual(200, self._post(self._bibtex(title=r"A \{safe\} title")).status_code)
        exported = self.client.get("/library/export/books.bib")
        self.assertEqual(200, exported.status_code)
        payload = exported.get_data()

        rows = bibtex_import_rows(payload)

        self.assertEqual(1, len(rows))
        self.assertEqual("A {safe} title", rows[0]["title"])
        self.assertEqual("9780306406157", rows[0]["isbn"])
        self.assertEqual("Novel", rows[0]["genre"])

    def test_multiple_entries_and_utf8_are_supported(self):
        payload = (
            "@book{one, title={Überprüfung}, author={Änne Autorin}}\n"
            "@book{two, title=\"Second\", isbn={9780000000002}}\n"
        ).encode()
        response = self._post(payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.get_json()["created"])

    def test_invalid_or_executable_syntax_rejects_whole_upload(self):
        cases = (
            b"not bibtex",
            b"@article{x, title={Article}}",
            b"@book{x, author={Untitled}}",
            b"@book{x, title={Open}",
            b"@book{x, title=macro}",
            b"@book{x, title={A}, year={1}, year={2}}",
            b"@string{x={value}}",
            ("@book{x, title={" + ("x" * 4001) + "}}").encode(),
            b"\xff\xfe",
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self._post(payload)
                self.assertEqual(400, response.status_code)
                self.assertFalse(response.get_json()["ok"])
                self.assertEqual([], ObjectStore(self.root).objects())

    def test_csrf_permissions_and_upload_limit_are_enforced(self):
        self.assertEqual(403, self._post(self._bibtex(), csrf=False).status_code)
        with self.client.session_transaction() as state:
            state["is_admin"] = False
        self.assertEqual(403, self._post(self._bibtex()).status_code)
        with self.client.session_transaction() as state:
            state["is_admin"] = True
        self.assertEqual(400, self._post(b"x" * (4 * 1024 * 1024 + 1)).status_code)

    def test_unauthenticated_import_is_redirected(self):
        anonymous = self.app.test_client()
        with anonymous.session_transaction() as state:
            state["_csrf_token"] = "bibtex-import-csrf"
        response = anonymous.post(
            "/library/import/books.bib", data={"_csrf_token": "bibtex-import-csrf"},
        )
        self.assertEqual(302, response.status_code)


if __name__ == "__main__":
    unittest.main()
