"""i18n batch 0: EN default, catalog parity, login selector, profile switch.

Covers the translation catalog (every de key has a non-empty en twin
and vice versa), the EN-first login page with its public DE/EN
selector, and the session-language switch (login page + settings
profile section). Live browsers are NOT verified here.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from app.settings_store import TRANSLATIONS, translate


class CatalogParityTest(unittest.TestCase):
    def test_every_key_exists_in_both_languages(self) -> None:
        de_keys = set(TRANSLATIONS["de"])
        en_keys = set(TRANSLATIONS["en"])
        self.assertEqual([], sorted(de_keys - en_keys))
        self.assertEqual([], sorted(en_keys - de_keys))

    def test_no_empty_values(self) -> None:
        for language in ("de", "en"):
            for key, value in TRANSLATIONS[language].items():
                self.assertTrue(str(value).strip(), f"{language}:{key}")

    def test_unknown_language_falls_back_to_english(self) -> None:
        self.assertEqual(
            TRANSLATIONS["en"]["auth.login.heading"], translate("fr", "auth.login.heading")
        )

    def test_missing_key_returns_key(self) -> None:
        self.assertEqual("auth.missing.xyz", translate("en", "auth.missing.xyz"))


class AuthLanguageWebTest(unittest.TestCase):
    password = "i18n-auth-test-password"

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

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/auth/login") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def login(self):
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password,
                  "_csrf_token": self.csrf(self.client)},
        )
        return self.client

    def test_login_defaults_to_english_with_selector(self) -> None:
        body = self.client.get("/auth/login").data.decode("utf-8")
        self.assertIn("<h1>Sign in</h1>", body)
        self.assertIn('action="/auth/language"', body)
        self.assertIn('value="de"', body)
        self.assertIn('value="en"', body)
        self.assertNotIn("<h1>Anmelden</h1>", body)

    def test_public_language_switch_turns_login_german(self) -> None:
        token = self.csrf(self.client)
        response = self.client.post(
            "/auth/language", data={"_csrf_token": token, "language": "de"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        body = response.data.decode("utf-8")
        self.assertIn("<h1>Anmelden</h1>", body)
        self.assertNotIn("<h1>Sign in</h1>", body)

    def test_login_error_message_follows_session_language(self) -> None:
        token = self.csrf(self.client)
        self.client.post(
            "/auth/language", data={"_csrf_token": token, "language": "de"})
        token = self.csrf(self.client)
        response = self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong",
                  "_csrf_token": token},
        )
        self.assertIn("Benutzername oder Passwort ist falsch",
                      response.data.decode("utf-8"))

    def test_selected_language_survives_successful_login(self) -> None:
        token = self.csrf(self.client)
        self.client.post(
            "/auth/language",
            data={"_csrf_token": token, "language": "de"},
        )
        token = self.csrf(self.client)
        response = self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password,
                  "_csrf_token": token},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn('aria-label="Hauptnavigation"', response.data.decode("utf-8"))

    def _bootstrap_client(self):
        with app.app_context():
            db = get_db()
            db.execute("DELETE FROM user")
            db.commit()
        return app.test_client()

    def test_register_page_english_then_german(self) -> None:
        fresh = self._bootstrap_client()
        body = fresh.get("/auth/register").data.decode("utf-8")
        self.assertIn("<h1>Create account</h1>", body)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', body.encode())
        self.assertIsNotNone(match)
        assert match is not None
        fresh.post("/auth/language",
                   data={"_csrf_token": match.group(1).decode(), "language": "de"})
        body = fresh.get("/auth/register").data.decode("utf-8")
        self.assertIn("<h1>Konto erstellen</h1>", body)

    def test_register_error_message_german(self) -> None:
        fresh = self._bootstrap_client()
        match = re.search(rb'name="_csrf_token" value="([^"]+)"',
                          fresh.get("/auth/register").data)
        assert match is not None
        fresh.post("/auth/language",
                   data={"_csrf_token": match.group(1).decode(), "language": "de"})
        match = re.search(rb'name="_csrf_token" value="([^"]+)"',
                          fresh.get("/auth/register").data)
        assert match is not None
        response = fresh.post(
            "/auth/register",
            data={"username": "", "password": "short",
                  "_csrf_token": match.group(1).decode()},
        )
        self.assertIn("Benutzername fehlt",
                      response.data.decode("utf-8"))

    def test_settings_offers_session_language_switch(self) -> None:
        client = self.login()
        body = client.get("/documents/settings").data.decode("utf-8")
        self.assertIn('id="session-language"', body)
        self.assertIn("Session language", body)

    def test_settings_switch_turns_german(self) -> None:
        client = self.login()
        token = self.csrf(client, "/documents/settings")
        response = client.post(
            "/documents/settings/language",
            data={"_csrf_token": token, "language": "de"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("Sitzungssprache", response.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
