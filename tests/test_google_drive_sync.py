from app.sqlite_utils import connect as sqlite_connect
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.google_drive_client import DriveClient, _validate_google_url
from app.google_drive_schema import ensure_google_drive_schema
from app.google_drive_sync import safe_drive_name, sync_decision
from app.google_tokens import GOOGLE_DRIVE_SCOPE


ROOT = Path(__file__).resolve().parents[1]


class GoogleDriveSyncTests(unittest.TestCase):
    def test_drive_scope_is_least_privilege_file_scope(self):
        self.assertEqual("https://www.googleapis.com/auth/drive.file", GOOGLE_DRIVE_SCOPE)

    def test_google_api_url_validation_rejects_lookalikes_and_plain_http(self):
        self.assertEqual(
            "https://www.googleapis.com/drive/v3/files?id=1",
            _validate_google_url("https://www.googleapis.com/drive/v3/files?id=1"),
        )
        for url in (
            "http://www.googleapis.com/drive/v3/files",
            "https://www.googleapis.com.evil.invalid/drive/v3/files",
            "https://user:secret@www.googleapis.com/drive/v3/files",
            "https://www.googleapis.com:444/drive/v3/files",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _validate_google_url(url)

    def test_drive_filename_is_safe_for_local_filesystem(self):
        self.assertEqual("Ordner_Datei_.pdf", safe_drive_name('Ordner/Datei?.pdf', "abc"))
        self.assertEqual("_CON.txt", safe_drive_name("CON.txt", "abc"))
        self.assertNotIn("/", safe_drive_name("../../private.txt", "abc"))
        self.assertLessEqual(len(safe_drive_name("a" * 300 + ".pdf", "abc")), 180)

    def test_sync_decision_never_overwrites_two_sided_change(self):
        self.assertEqual("same", sync_decision("a", "a", "1", "1"))
        self.assertEqual("upload", sync_decision("b", "a", "1", "1"))
        self.assertEqual("download", sync_decision("a", "a", "2", "1"))
        self.assertEqual("conflict", sync_decision("b", "a", "2", "1"))

    def test_multipart_upload_keeps_metadata_and_payload_separate(self):
        body, content_type = DriveClient._multipart(
            {"name": "test.txt", "parents": ["root"]}, b"hello", "text/plain"
        )
        self.assertIn("multipart/related; boundary=", content_type)
        self.assertIn(b'"name": "test.txt"', body)
        self.assertIn(b"Content-Type: text/plain", body)
        self.assertIn(b"hello", body)

    def test_drive_schema_is_additive_and_has_unique_local_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Flask(__name__)
            database = Path(temp) / "drive.sqlite"
            app.config["DATABASE"] = str(database)
            with app.app_context():
                ensure_google_drive_schema()
                ensure_google_drive_schema()
            connection = sqlite_connect(database)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
            finally:
                connection.close()
            self.assertIn("google_drive_state", tables)
            self.assertIn("google_drive_link", tables)
            self.assertNotIn("google_drive_oauth_handoff", tables)
            self.assertIn("google_drive_link_local", indexes)

    def test_android_token_handoff_is_loopback_session_and_scope_bound(self):
        source = (ROOT / "app" / "google_drive_admin.py").read_text(encoding="utf-8")
        self.assertIn('@bp.post("/android-token")', source)
        self.assertIn('request.remote_addr not in {"127.0.0.1", "::1"}', source)
        self.assertIn('ANDROID_USER_AGENT_TOKEN = "SimpleOffice4Me-Android/"', source)
        self.assertIn('MAX_ANDROID_TOKEN_BYTES = 8192', source)
        self.assertIn('MAX_ANDROID_TOKEN_REQUEST_BYTES = 16 * 1024', source)
        self.assertIn('action not in ANDROID_ACTIONS', source)
        self.assertIn('GOOGLE_DRIVE_SCOPE not in scopes', source)
        self.assertIn('"expires_in": 3000', source)
        self.assertIn('_store_drive_token(', source)
        self.assertIn('sync_google_drive(g.user["id"]', source)
        self.assertNotIn("google_drive_oauth_handoff", source)
        self.assertNotIn("code_verifier", source)

    def test_android_authorization_is_native_and_never_custom_scheme_token_transport(self):
        java = (ROOT / "android" / "apk" / "app" / "src" / "main" / "java" / "de" / "simpleoffice4me" / "android" / "AndroidGoogleAuthorization.java").read_text(encoding="utf-8")
        gradle = (ROOT / "android" / "apk" / "app" / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("play-services-auth:21.6.0", gradle)
        self.assertIn("AuthorizationRequest.builder()", java)
        self.assertIn("setRequestedScopes(Arrays.asList(new Scope(DRIVE_SCOPE)))", java)
        self.assertIn("Identity.getAuthorizationClient(activity)", java)
        self.assertIn("result.getAccessToken()", java)
        self.assertIn("result.getGrantedScopes()", java)
        self.assertIn("/settings/google-drive/android-token", java)
        self.assertIn('setRequestProperty("X-CSRF-Token", csrf)', java)
        self.assertIn('setRequestProperty("Cookie", cookie)', java)
        self.assertIn("connection.setInstanceFollowRedirects(false)", java)
        self.assertNotIn("simpleoffice4me://", java)
        self.assertNotIn("requestOfflineAccess", java)
        self.assertFalse((ROOT / "app" / "google_drive_oauth_handoff.py").exists())


if __name__ == "__main__":
    unittest.main()