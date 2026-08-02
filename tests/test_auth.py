import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import MIB, app, configured_upload_limit_bytes, google_oauth_credentials
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
    def test_upload_limit_configuration_is_bounded(self):
        with patch.dict("os.environ", {"SIMPLEOFFICE_MAX_UPLOAD_MIB": "1024"}, clear=True):
            self.assertEqual(1024 * MIB, configured_upload_limit_bytes())
        with patch.dict("os.environ", {"SIMPLEOFFICE_MAX_UPLOAD_MIB": "invalid"}, clear=True):
            self.assertEqual(512 * MIB, configured_upload_limit_bytes())
        with patch.dict("os.environ", {"SIMPLEOFFICE_MAX_UPLOAD_MIB": "99999"}, clear=True):
            self.assertEqual(4096 * MIB, configured_upload_limit_bytes())

    def test_google_web_oauth_json_is_loaded_from_protected_file(self):
        with tempfile.TemporaryDirectory() as temp:
            credentials_file = Path(temp) / "google-client.json"
            credentials_file.write_text(json.dumps({"web": {"client_id": "json-client", "client_secret": "json-secret", "redirect_uris": ["https://office.example.test/auth/google/callback"]}}), encoding="utf-8")
            with patch.dict("os.environ", {"SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE": str(credentials_file)}, clear=True):
                self.assertEqual(("json-client", "json-secret"), google_oauth_credentials())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI", "GOOGLE_OAUTH_REDIRECT_URIS", "MAX_CONTENT_LENGTH", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "auth.sqlite"), DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"), GOOGLE_OAUTH_CLIENT_ID="test.apps.googleusercontent.com", GOOGLE_OAUTH_CLIENT_SECRET="secret", GOOGLE_OAUTH_REDIRECT_URI="https://example.test/auth/google/callback", GOOGLE_OAUTH_REDIRECT_URIS=())
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_google_json_callback_is_used_when_request_url_is_not_configured(self):
        app.config.update(GOOGLE_OAUTH_REDIRECT_URI="", GOOGLE_OAUTH_REDIRECT_URIS=("https://office.example.test/auth/google/callback",))
        with app.test_request_context("/auth/google", base_url="http://127.0.0.1:8080"):
            self.assertEqual("https://office.example.test/auth/google/callback", auth._google_config()["redirect_uri"])

    def test_oversized_request_returns_413_with_configured_limit(self):
        app.config["MAX_CONTENT_LENGTH"] = 64
        response = self.client.post(
            "/auth/register",
            data={"username": "j" * 100, "password": "sicheres-passwort"},
        )
        self.assertEqual(413, response.status_code)
        self.assertIn("Upload-Limit", response.get_data(as_text=True))

    def test_local_registration_and_google_registration(self):
        response = self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.assertEqual(302, response.status_code)
        self.assertEqual(302, self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"}).status_code)

        start = self.client.get("/auth/google")
        self.assertEqual(302, start.status_code)
        with self.client.session_transaction() as session:
            state = session["google_oauth_state"]
        responses = iter([_Response({"access_token": "access"}), _Response({"sub": "google-subject", "email": "jens@example.test", "name": "Jens Google", "email_verified": True})])
        with patch.object(auth, "urlopen", side_effect=lambda *args, **kwargs: next(responses)), patch.object(auth, "sync_google_account", return_value={"contacts": 0, "events": 0, "calendars": 0}):
            callback = self.client.get(f"/auth/google/callback?code=code&state={state}")
        self.assertEqual(302, callback.status_code)
        with app.app_context():
            identity = database.get_db().execute("SELECT provider, subject, email FROM oauth_identity").fetchone()
            profile = database.get_db().execute("SELECT display_name, email FROM user WHERE username = ?", ("jens-2",)).fetchone()
        self.assertEqual(("google", "google-subject", "jens@example.test"), tuple(identity))
        self.assertEqual(("Jens Google", "jens@example.test"), tuple(profile))
