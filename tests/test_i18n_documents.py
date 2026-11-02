"""i18n batch 3: documents contacts page renders DE + EN.

Static template strings follow the file-local ternary convention
(documents area); option values and stored labels stay German data.
Live browsers are NOT verified here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class ContactsLanguageWebTest(unittest.TestCase):
    password = "i18n-contacts-test-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.client = app.test_client()
        import re
        match = re.search(rb'name="_csrf_token" value="([^"]+)"',
                          self.client.get("/auth/login").data)
        assert match is not None
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password,
                  "_csrf_token": match.group(1).decode()},
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _get(self, language: str = "en") -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = self.client.get("/documents/contacts")
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_contacts_english(self) -> None:
        body = self._get("en")
        self.assertIn("Contact list", body)
        self.assertIn("Create contact", body)
        self.assertIn("Import contact", body)
        self.assertIn("Save contact", body)

    def test_contacts_german(self) -> None:
        body = self._get("de")
        self.assertIn("Kontaktliste", body)
        self.assertIn("Kontakt anlegen", body)
        self.assertIn("Kontakt importieren", body)
        self.assertIn("Kontakt speichern", body)


if __name__ == "__main__":
    unittest.main()
