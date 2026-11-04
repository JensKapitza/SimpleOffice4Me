"""i18n batch 1: ContextFlow templates render DE + EN from the catalog.

Renders the dashboard and the agent page in both languages and
asserts the translated headings, pills, and form labels. Dynamic
backend strings (reasons, titles, amounts) stay German for now and
are explicitly out of scope here. Live browsers are NOT verified.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class ContextflowLanguageWebTest(unittest.TestCase):
    password = "test-only-i18n-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "TESTING",
                "TEST_CSRF_PROTECTION", "CONTEXTFLOW_ENABLED",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
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

    def _get(self, path: str, language: str = "en") -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_dashboard_english(self) -> None:
        body = self._get("/contextflow/")
        self.assertIn("Office angel – evidenced notices", body)
        self.assertIn("Local rule operation", body)
        self.assertIn("No confirmed cases yet.", body)
        self.assertIn("Quiet hours &amp; notices per day", body)
        self.assertIn("Confirm linking", body)

    def test_dashboard_german(self) -> None:
        body = self._get("/contextflow/", "de")
        self.assertIn("Büroengel – belegte Hinweise", body)
        self.assertIn("Lokaler Regelbetrieb", body)
        self.assertIn("Noch keine bestätigten Fälle.", body)
        self.assertIn("Ruhezeiten &amp; Hinweise pro Tag", body)
        self.assertIn("Zuordnung bestätigen", body)

    def test_agent_page_english(self) -> None:
        body = self._get("/contextflow/agent")
        self.assertIn("Meta demo analysis (synthetic)", body)
        self.assertIn("Choose a case and consent", body)
        self.assertIn("No confirmed case yet.", body)
        self.assertIn("Start analysis", body)

    def test_agent_page_german(self) -> None:
        body = self._get("/contextflow/agent", "de")
        self.assertIn("Meta-Demo-Analyse (synthetisch)", body)
        self.assertIn("Fall wählen und einwilligen", body)
        self.assertIn("Noch kein bestätigter Fall vorhanden.", body)
        self.assertIn("Analyse starten", body)


if __name__ == "__main__":
    unittest.main()
