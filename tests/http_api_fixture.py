"""Shared HTTP caller setup; no domain fixtures or computed expected results."""
from __future__ import annotations

import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from app import app
from app.db import ensure_auth_database


class CsrfMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "csrf-token":
            self.token = attributes.get("content", "")


class PublicHttpTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        previous = app.config.copy()
        self.addCleanup(self._restore_config, previous)
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=True,
            DATABASE=str(root / "auth.sqlite3"), DOCUMENT_ROOT=str(root / "documents"),
        )
        # Ordinary application-start schema initialization, never fixture INSERTs.
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self._register(self.client, "owner")
        self._login(self.client, "owner")

    @staticmethod
    def _restore_config(previous):
        app.config.clear()
        app.config.update(previous)

    def _csrf_headers(self, client, path="/tasks/"):
        page = client.get(path)
        self.assertEqual(200, page.status_code)
        parser = CsrfMetadata()
        parser.feed(page.get_data(as_text=True))
        self.assertTrue(parser.token, "Public HTML must supply the browser CSRF token")
        return {"X-CSRF-Token": parser.token}

    def _register(self, client, username):
        response = client.post(
            "/auth/register",
            data={"username": username, "password": "test-only-api-password"},
            headers=self._csrf_headers(client, "/auth/register"),
        )
        self.assertEqual(302, response.status_code)

    def _login(self, client, username):
        response = client.post(
            "/auth/login",
            data={"username": username, "password": "test-only-api-password"},
            headers=self._csrf_headers(client, "/auth/login"),
        )
        self.assertEqual(302, response.status_code)
