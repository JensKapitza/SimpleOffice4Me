"""ContextFlow redirects stay on repository-owned routes."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest import mock

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class ContextFlowRedirectSafetyTests(unittest.TestCase):
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
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) "
                "VALUES(?,?,?,1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("redirect-admin", hash_password("synthetic-redirect-test-password"), "Admin"),
            )
            db.commit()
        self.client = app.test_client()
        login_page = self.client.get("/auth/login")
        token = re.search(rb'name="_csrf_token" value="([^"]+)"', login_page.data)
        self.assertIsNotNone(token)
        assert token is not None
        self.client.post(
            "/auth/login",
            data={
                "username": "redirect-admin",
                "password": "synthetic-redirect-test-password",
                "_csrf_token": token.group(1).decode(),
            },
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_confirmed_visit_redirect_is_built_from_local_endpoint(self) -> None:
        page = self.client.get("/contextflow/")
        token = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(token)
        assert token is not None
        with mock.patch(
            "app.contextflow.web.confirm_draft",
            return_value={"visit_id": "//attacker.invalid"},
        ):
            response = self.client.post(
                "/contextflow/suggest/confirm",
                data={
                    "visit_id": "synthetic-visit",
                    "approved": "yes",
                    "title": "Finding",
                    "description": "Synthetic",
                    "evidence_class": "visual",
                    "confidence": "90",
                    "expected_fingerprint": "f" * 64,
                    "_csrf_token": token.group(1).decode(),
                },
            )

        self.assertEqual(302, response.status_code)
        target = urlsplit(response.location)
        self.assertEqual("", target.scheme)
        self.assertEqual("", target.netloc)
        self.assertTrue(target.path.startswith("/site-visits/"))


if __name__ == "__main__":
    unittest.main()
