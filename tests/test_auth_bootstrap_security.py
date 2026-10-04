"""First-account privileges and registration admission are serialized."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app import app, auth
from app.db import ensure_auth_database, get_db


class BootstrapSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "TESTING", "ALLOW_PUBLIC_REGISTRATION",
                "GOOGLE_OAUTH_AUTO_PROVISION", "GOOGLE_OAUTH_CLIENT_ID",
                "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
            )
        }
        app.config.update(
            TESTING=False,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            ALLOW_PUBLIC_REGISTRATION=False,
            GOOGLE_OAUTH_AUTO_PROVISION=False,
            GOOGLE_OAUTH_CLIENT_ID="test-client",
            GOOGLE_OAUTH_CLIENT_SECRET="test-client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="https://office.example.test/auth/google/callback",
        )
        with app.app_context():
            ensure_auth_database()

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def _register(self, username):
        client = app.test_client()
        client.get("/auth/register")
        with client.session_transaction() as session:
            token = session["_csrf_token"]
        return client.post(
            "/auth/register",
            data={"username": username, "password": "bootstrap-test-password", "_csrf_token": token},
        )

    def _users(self):
        with app.app_context():
            return [dict(row) for row in get_db().execute("SELECT username,is_admin FROM user ORDER BY id")]

    def _concurrent_registration(self, public):
        app.config["ALLOW_PUBLIC_REGISTRATION"] = public
        barrier = threading.Barrier(2)
        real_hash = auth.hash_password

        def synchronized_hash(password):
            hashed = real_hash(password)
            barrier.wait(timeout=10)
            return hashed

        with patch.object(auth, "hash_password", side_effect=synchronized_hash):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(self._register, ("first", "second")))
        return sorted(response.status_code for response in responses)

    def test_concurrent_first_registrations_close_admission_after_one_account(self):
        self.assertEqual([302, 403], self._concurrent_registration(public=False))
        users = self._users()
        self.assertEqual(1, len(users))
        self.assertEqual(1, users[0]["is_admin"])

    def test_public_registration_still_grants_only_one_bootstrap_admin(self):
        self.assertEqual([302, 302], self._concurrent_registration(public=True))
        users = self._users()
        self.assertEqual(2, len(users))
        self.assertEqual(1, sum(user["is_admin"] for user in users))

    def _google_callback(self, client):
        client.get("/auth/google")
        with client.session_transaction() as session:
            state = session["google_oauth_state"]
        return client.get("/auth/google/callback", query_string={"code": "test-code", "state": state})

    @staticmethod
    def _google_response(url, **_kwargs):
        if url == auth.GOOGLE_TOKEN_URL:
            return {"access_token": "test-access-token"}
        return {"sub": "test-subject", "email": "google@example.test", "email_verified": True}

    def test_google_bootstrap_rechecks_admission_after_local_account_creation(self):
        real_hash = auth.hash_password
        interleaved = False

        def interleaved_hash(password):
            nonlocal interleaved
            if not interleaved:
                interleaved = True
                self.assertEqual(302, self._register("owner").status_code)
            return real_hash(password)

        with patch.object(auth, "_google_json_request", side_effect=self._google_response):
            with patch.object(auth, "hash_password", side_effect=interleaved_hash):
                with patch.object(auth, "sync_google_account") as sync:
                    response = self._google_callback(app.test_client())
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/auth/login"))
        sync.assert_not_called()
        self.assertEqual([{"username": "owner", "is_admin": 1}], self._users())

    def test_overlapping_callbacks_reuse_the_same_google_identity(self):
        real_hash = auth.hash_password
        interleaved = False

        def interleaved_hash(password):
            nonlocal interleaved
            if not interleaved:
                interleaved = True
                self.assertEqual(302, self._google_callback(app.test_client()).status_code)
            return real_hash(password)

        with patch.object(auth, "_google_json_request", side_effect=self._google_response):
            with patch.object(auth, "sync_google_account", return_value={"contacts": 0, "events": 0, "calendars": 0}):
                with patch.object(auth, "hash_password", side_effect=interleaved_hash):
                    response = self._google_callback(app.test_client())
        self.assertEqual(302, response.status_code)
        self.assertEqual(1, len(self._users()))
        with app.app_context():
            self.assertEqual(1, get_db().execute("SELECT COUNT(*) FROM oauth_identity").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
