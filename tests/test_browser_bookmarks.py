from __future__ import annotations

import unittest
import tempfile
from io import BytesIO
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password

from app.browser_bookmarks import _search_pattern


class BookmarkSearchPatternTests(unittest.TestCase):
    def test_search_pattern_escapes_like_metacharacters_and_escape_character(self):
        self.assertEqual("%100!%%", _search_pattern("100%"))
        self.assertEqual("%file!_part%", _search_pattern("file_part"))
        self.assertEqual("%wow!!%", _search_pattern("wow!"))
        self.assertEqual("%!%!_!!%", _search_pattern("%_!"))


class BookmarkTranslationTests(unittest.TestCase):
    password = "bookmark-i18n-test-password"

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
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            ensure_auth_database()
            get_db().execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at) "
                "VALUES (?,?,?,1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("bookmark-admin", hash_password(self.password), "Admin"),
            )
            get_db().commit()
        self.client = app.test_client()
        self.client.post(
            "/auth/login",
            data={"username": "bookmark-admin", "password": self.password},
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _language(self, language: str) -> None:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language

    def test_bookmark_page_renders_in_both_languages(self) -> None:
        for language, expected, absent in (
            ("en", "Add bookmark", "Lesezeichen hinzufügen"),
            ("de", "Lesezeichen hinzufügen", "Add bookmark"),
        ):
            with self.subTest(language=language):
                self._language(language)
                response = self.client.get("/bookmarks/")
                self.assertEqual(200, response.status_code)
                body = response.get_data(as_text=True)
                self.assertIn(expected, body)
                self.assertNotIn(absent, body)

    def test_bookmark_flash_and_import_preview_follow_selected_language(self) -> None:
        cases = (("en", "Bookmark saved.", "Review Firefox import"),
                 ("de", "Lesezeichen gespeichert.", "Firefox-Import prüfen"))
        for language, saved_message, preview_heading in cases:
            with self.subTest(language=language):
                self._language(language)
                response = self.client.post(
                    "/bookmarks/add",
                    data={"title": "Documentation", "url": f"https://example.org/docs-{language}"},
                    follow_redirects=True,
                )
                self.assertEqual(200, response.status_code)
                self.assertIn(saved_message, response.get_data(as_text=True))
                response = self.client.post(
                    "/bookmarks/import",
                    data={"file": (  # Small Firefox HTML export creates an import draft.
                        BytesIO(b'''<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><A HREF="https://example.org/import">Docs</A></DL>'''),
                        "bookmarks.html",
                    )},
                    content_type="multipart/form-data",
                    follow_redirects=True,
                )
                self.assertEqual(200, response.status_code)
                self.assertIn(preview_heading, response.get_data(as_text=True))
