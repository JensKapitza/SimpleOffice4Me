"""HTTP and round-trip contracts for bounded BibTeX book import."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g, session

from app import db as database
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
        return f"""@book{{simpleoffice_book,
  title = {{{title}}},
  author = {{Ada Author and Ben Writer}},
  publisher = {{Publisher}},
  year = {{2024}},
  edition = {{2}},
  series = {{Series}},
  volume = {{3}},
  note = {{Inventory ID: 7; Library location: Imported shelf; Pages: 224; ISBN: {isbn}; Language: de; Genre: Novel; Description: Description}}
}}
""".encode()

    def _post(self, payload=None, *, csrf=True):
        data = {}
        if csrf:
            data["_csrf_token"] = "bibtex-import-csrf"
        if payload is not None:
            data["file"] = (io.BytesIO(payload), "books.bib")
        return self.client.post("/library/import/books.bib", data=data)

    def test_import_maps_metadata_round_trips_note_and_skips_duplicate(self):
        first = self._post(self._bibtex(title=r"Title \{one\}"))
        second = self._post(self._bibtex())

        self.assertEqual(200, first.status_code)
        self.assertEqual({"ok": True, "created": 1, "skipped": 0, "total": 1}, first.get_json())
        self.assertEqual({"ok": True, "created": 0, "skipped": 1, "total": 1}, second.get_json())
        book = ObjectStore(self.root).objects()[0]
        self.assertEqual("Title {one}", book["name"])
        self.assertEqual("Ada Author; Ben Writer", book["fields"]["authors"])
        self.assertEqual("Series", book["fields"]["series"])
        self.assertEqual("Novel", book["fields"]["genre"])
        self.assertEqual("Description", book["fields"]["description"])
        self.assertEqual(
            "Inventory ID: 7; Library location: Imported shelf; Pages: 224",
            book["fields"]["bibliography_note"],
        )

        exported = self.client.get("/library/export/books.bib")
        self.assertEqual(200, exported.status_code)
        self.assertIn(r"title = {Title \{one\}}", exported.get_data(as_text=True))
        self.assertIn("Library location: Imported shelf", exported.get_data(as_text=True))

    def test_utf8_quoted_values_and_standard_fields_are_supported(self):
        payload = (
            '% comment\n@BOOK(key, title="Überprüfung", author="Änne and Björn", '
            'isbn=123, keywords={Fachbuch}, language={de}, abstract={Beschreibung})'
        ).encode()
        response = self._post(payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.get_json()["created"])
        book = ObjectStore(self.root).objects()[0]
        self.assertEqual("Überprüfung", book["name"])
        self.assertEqual("Änne; Björn", book["fields"]["authors"])
        self.assertEqual("123", book["fields"]["isbn"])

    def test_invalid_second_record_rejects_the_whole_upload(self):
        response = self._post(self._bibtex(isbn="111") + b"@article{x,title={Not a book}}")

        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual([], ObjectStore(self.root).objects())

    def test_malformed_ambiguous_and_active_constructs_are_rejected(self):
        cases = (
            b"not bibtex",
            b"@book{x, author={No title}}",
            b"@book{x, title={One}, title={Two}}",
            b"@book{x, title={Missing end}",
            b"@book{x, title={One} # {Two}}",
            b"@string{x={command}}",
            b"\xff\xfe",
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self._post(payload)
                self.assertEqual(400, response.status_code)
                self.assertFalse(response.get_json()["ok"])

    def test_csrf_permissions_and_upload_limit_are_enforced(self):
        self.assertEqual(403, self._post(self._bibtex(), csrf=False).status_code)
        with self.client.session_transaction() as state:
            state["is_admin"] = False
        self.assertEqual(403, self._post(self._bibtex()).status_code)
        with self.client.session_transaction() as state:
            state["is_admin"] = True
        too_large = b"x" * (4 * 1024 * 1024 + 1)
        self.assertEqual(400, self._post(too_large).status_code)

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
