import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.google_drive_client import DriveClient, _validate_google_url
from app.google_drive_schema import ensure_google_drive_schema
from app.google_drive_sync import safe_drive_name, sync_decision
from app.google_tokens import GOOGLE_DRIVE_SCOPE


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
            connection = sqlite3.connect(database)
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
            self.assertIn("google_drive_link_local", indexes)


if __name__ == "__main__":
    unittest.main()
