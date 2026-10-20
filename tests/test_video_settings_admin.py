"""Authorization, CSRF and audit contracts for video preview settings."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.settings_store import DEFAULT_VIDEO_PREVIEW_FRAMES, SettingsStore


class VideoSettingsAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="video-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("video-admin", 1), ("video-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post("/auth/login", data={"username": "video-admin", "password": "video-admin"})
        self.user.post("/auth/login", data={"username": "video-user", "password": "video-user"})
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.config)
        self.temp.cleanup()

    def test_video_settings_form_has_csrf_token_and_default_reset(self):
        document = {"document_id": "video-one", "last_path": "clip.mp4", "preview": {}}
        with patch("app.documents_routes_video._video_document", return_value=document), patch(
            "app.documents_routes_video.detect_preview_tools",
            return_value={"commands": {"ffmpeg": "/fake/ffmpeg", "ffprobe": "/fake/ffprobe"}},
        ):
            response = self.admin.get("/documents/video-one/video")

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('name="_csrf_token"', html)
        self.assertIn('name="reset" value="1"', html)

    def test_save_requires_admin_and_csrf_then_persists_and_audits(self):
        path = "/documents/video-one/video/settings"
        with patch("app.documents_routes_video._video_document", return_value={"document_id": "video-one"}):
            missing_token = self.admin.post(path, data={"frame_count": "7"})
            non_admin = self.user.post(
                path,
                data={"frame_count": "7"},
                headers={"X-CSRF-Token": "u" * 40},
            )
            saved = self.admin.post(
                path,
                data={"frame_count": "7"},
                headers={"X-CSRF-Token": "a" * 40},
            )
            reset = self.admin.post(
                path,
                data={"reset": "1"},
                headers={"X-CSRF-Token": "a" * 40},
            )

        self.assertEqual(403, missing_token.status_code)
        self.assertEqual(403, non_admin.status_code)
        self.assertEqual(302, saved.status_code)
        self.assertEqual(302, reset.status_code)
        self.assertEqual(
            DEFAULT_VIDEO_PREVIEW_FRAMES,
            SettingsStore(self.root).settings()["media"]["video_preview_frames"],
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_type,target_id,detail FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("video_preview_settings_updated",),
            ).fetchone()
        self.assertEqual("video-admin", row["actor_name"])
        self.assertEqual("settings", row["target_type"])
        self.assertEqual("media.video_preview_frames", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])


if __name__ == "__main__":
    unittest.main()
