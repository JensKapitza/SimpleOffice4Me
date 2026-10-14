"""HTTP and round-trip contracts for bounded RIS book import."""
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


class RisImportHttpContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__, template_folder="../templates", static_folder="../static")
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DATABASE=str(self.root / "users.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="ris-import-test",
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
            state["_csrf_token"] = "ris-import-csrf"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _ris(kind="BOOK", *, title="Imported title", isbn="9780306406157"):
        return (
            f"TY  - {kind}\r\n"
            f"T1  - {title}\r\n"
            "AU  - Ada Author\r\n"
            "AU  - Ben Writer\r\n"
            f"SN  - {isbn}\r\n"
            "PB  - Publisher\r\n"
            "PY  - 2024\r\n"
            "LA  - de\r\n"
            "ET  - 2\r\n"
            "T3  - Series\r\n"
            "VL  - 3\r\n"
            "KW  - Novel\r\n"
            "AB  - Description\r\n"
            "N1  - Library location: Imported shelf\r\n"
            "ER  -\r\n"
        ).encode()

    def _post(self, payload=None, *, csrf=True):
        data = {}
        if csrf:
            data["_csrf_token"] = "ris-import-csrf"
        if payload is not None:
            data["file"] = (io.BytesIO(payload), "books.ris")
        return self.client.post("/library/import/books.ris", data=data)

    def test_import_maps_metadata_preserves_note_and_skips_duplicate(self):
        first = self._post(self._ris())
        second = self._post(self._ris())

        self.assertEqual(200, first.status_code)
        self.assertEqual({"ok": True, "created": 1, "skipped": 0, "total": 1}, first.get_json())
        self.assertEqual({"ok": True, "created": 0, "skipped": 1, "total": 1}, second.get_json())
        book = ObjectStore(self.root).objects()[0]
        self.assertEqual("Ada Author; Ben Writer", book["fields"]["authors"])
        self.assertEqual("Series", book["fields"]["series"])
        self.assertEqual("Library location: Imported shelf", book["fields"]["bibliography_note"])

        exported = self.client.get("/library/export/books.ris")
        self.assertEqual(200, exported.status_code)
        self.assertIn("N1  - Inventory ID: 1; Library location: Imported shelf", exported.get_data(as_text=True))

    def test_utf8_ebook_and_alternative_title_are_supported(self):
        payload = "TY  - EBOOK\nTI  - Überprüfung\nA1  - Änne Autorin\nER  -\n".encode()
        response = self._post(payload)

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.get_json()["created"])
        self.assertEqual("Überprüfung", ObjectStore(self.root).objects()[0]["name"])

    def test_malformed_unfinished_and_non_book_records_are_rejected(self):
        cases = (
            b"not ris",
            b"TY  - BOOK\nT1  - Missing end\n",
            b"TY  - JOUR\nT1  - Article\nER  -\n",
            b"TY  - BOOK\nER  -\n",
            ("TY  - BOOK\nT1  - Title\nN1  - " + ("x" * 4001) + "\nER  -\n").encode(),
            b"\xff\xfe",
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self._post(payload)
                self.assertEqual(400, response.status_code)
                self.assertFalse(response.get_json()["ok"])

    def test_csrf_permissions_and_upload_limit_are_enforced(self):
        self.assertEqual(403, self._post(self._ris(), csrf=False).status_code)
        with self.client.session_transaction() as state:
            state["is_admin"] = False
        self.assertEqual(403, self._post(self._ris()).status_code)
        with self.client.session_transaction() as state:
            state["is_admin"] = True
        too_large = b"x" * (4 * 1024 * 1024 + 1)
        self.assertEqual(400, self._post(too_large).status_code)

    def test_unauthenticated_import_is_redirected(self):
        anonymous = self.app.test_client()
        with anonymous.session_transaction() as state:
            state["_csrf_token"] = "ris-import-csrf"
        response = anonymous.post(
            "/library/import/books.ris", data={"_csrf_token": "ris-import-csrf"},
        )
        self.assertEqual(302, response.status_code)


if __name__ == "__main__":
    unittest.main()
