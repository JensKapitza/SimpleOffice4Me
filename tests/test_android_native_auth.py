from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import session
from werkzeug.exceptions import Forbidden

from app import app
from app import android_auth
from app import db as database
from app.password_security import hash_password


class AndroidNativeAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "android-auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def android_env(self, *, email: str = "jens@example.test"):
        return patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_ANDROID": "1",
                "SIMPLEOFFICE_ANDROID_ACCOUNT": email,
                "SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN": "n" * 64,
            },
            clear=False,
        )

    def test_google_device_identity_provisions_one_local_user_without_password(self):
        with self.android_env(), app.app_context():
            first, _ = android_auth._android_user(create=True)
            second, _ = android_auth._android_user(create=True)
            db = database.get_db()
            users = db.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            account = db.execute(
                "SELECT identity, account_email, password_enabled FROM android_local_account"
            ).fetchone()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(1, users)
        self.assertEqual("jens@example.test", first["email"])
        self.assertEqual("android-google", first["profile_source"])
        self.assertEqual(("google:jens@example.test", "jens@example.test", 0), tuple(account))

    def test_no_google_account_still_provisions_local_android_user(self):
        with self.android_env(email=""), app.app_context():
            user, _ = android_auth._android_user(create=True)
            account = database.get_db().execute(
                "SELECT identity, account_email, password_enabled FROM android_local_account"
            ).fetchone()
        self.assertEqual("android-local", user["profile_source"])
        self.assertEqual("local:device", account["identity"])
        self.assertEqual(0, account["password_enabled"])

    def test_native_challenge_requires_loopback_secret(self):
        with self.android_env(), app.test_request_context(
            "/auth/android/challenge",
            headers={"X-SimpleOffice-Android-Token": "wrong" * 16},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            with self.assertRaises(Forbidden):
                android_auth.challenge()

        with self.android_env(), app.test_request_context(
            "/auth/android/challenge",
            headers={"X-SimpleOffice-Android-Token": "n" * 64},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            response = android_auth.challenge()
            self.assertTrue(response.get_json()["ok"])
            self.assertGreaterEqual(len(response.get_json()["csrf_token"]), 32)

    def test_unprotected_native_account_bootstrap_creates_logged_in_session(self):
        with self.android_env(), app.test_request_context(
            "/auth/android/bootstrap",
            method="POST",
            headers={"X-SimpleOffice-Android-Token": "n" * 64},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            response = android_auth.bootstrap()
            payload = response.get_json()
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["password_required"])
            self.assertIsNotNone(session.get("user_id"))

    def test_password_protected_native_account_rejects_wrong_and_accepts_correct_password(self):
        password = "correct horse battery staple"
        with self.android_env(), app.app_context():
            user, _ = android_auth._android_user(create=True)
            user_id = int(user["id"])
            db = database.get_db()
            db.execute(
                "UPDATE user SET password = ? WHERE id = ?",
                (hash_password(password), user_id),
            )
            db.execute(
                "UPDATE android_local_account SET password_enabled = 1 WHERE user_id = ?",
                (user_id,),
            )
            db.commit()

        with self.android_env(), app.test_request_context(
            "/auth/android/unlock",
            method="POST",
            json={"password": "definitely-wrong"},
            headers={"X-SimpleOffice-Android-Token": "n" * 64},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            response, status = android_auth.unlock()
            self.assertEqual(401, status)
            self.assertEqual("invalid_password", response.get_json()["error"])
            self.assertIsNone(session.get("user_id"))

        with self.android_env(), app.test_request_context(
            "/auth/android/unlock",
            method="POST",
            json={"password": password},
            headers={"X-SimpleOffice-Android-Token": "n" * 64},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            response = android_auth.unlock()
            self.assertTrue(response.get_json()["ok"])
            self.assertEqual(user_id, session.get("user_id"))


if __name__ == "__main__":
    unittest.main()
