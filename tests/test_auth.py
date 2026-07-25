import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import auth
from app import db as database


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "auth.sqlite"), DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"), GOOGLE_OAUTH_CLIENT_ID="test.apps.googleusercontent.com", GOOGLE_OAUTH_CLIENT_SECRET="secret", GOOGLE_OAUTH_REDIRECT_URI="https://example.test/auth/google/callback")
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_local_registration_and_google_registration(self):
        response = self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.assertEqual(302, response.status_code)
        self.assertEqual(302, self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"}).status_code)

        start = self.client.get("/auth/google")
        self.assertEqual(302, start.status_code)
        with self.client.session_transaction() as session:
            state = session["google_oauth_state"]
        responses = iter([_Response({"access_token": "access"}), _Response({"sub": "google-subject", "email": "jens@example.test", "email_verified": True})])
        with patch.object(auth, "urlopen", side_effect=lambda *args, **kwargs: next(responses)):
            callback = self.client.get(f"/auth/google/callback?code=code&state={state}")
        self.assertEqual(302, callback.status_code)
        with app.app_context():
            identity = database.get_db().execute("SELECT provider, subject, email FROM oauth_identity").fetchone()
        self.assertEqual(("google", "google-subject", "jens@example.test"), tuple(identity))
