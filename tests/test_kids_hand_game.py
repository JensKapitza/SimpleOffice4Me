"""Authenticated route and asset integrity checks; no camera or child data."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import app
from app import kids_hand_assets
from http_api_fixture import PublicHttpTestCase

GAME = "/kids/lernen/handzeichen"
MODEL = GAME + "/assets/hand_landmarker.task"


class KidsHandGameTests(PublicHttpTestCase):
    def test_real_kids_route_with_scoped_permissions_and_offline_fallback(self):
        entry = self.client.get("/kids/")
        self.assertEqual(entry.status_code, 200)
        self.assertIn(f'href="{GAME}"', entry.get_data(as_text=True))
        self.assertIn("camera=()", entry.headers["Permissions-Policy"])
        with patch.dict(app.config, {"KIDS_HAND_GAME_ASSET_DIR": "/nonexistent/missing-assets"}):
            game = self.client.get(GAME)
        text = game.get_data(as_text=True)
        self.assertIn("data-ready=\"no\"", text)
        self.assertIn("data-hand-camera", text)
        self.assertIn("docs/KIDS_HAND_GAME.md", text)
        self.assertIn("not sign language", text)
        self.assertIn("camera=(self), microphone=()", game.headers["Permissions-Policy"])
        self.assertIn("connect-src 'self'", game.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", game.headers["Content-Security-Policy"])
        self.assertIn("'wasm-unsafe-eval'", game.headers["Content-Security-Policy"])
        self.assertNotIn("'wasm-unsafe-eval'", entry.headers["Content-Security-Policy"])
        self.assertNotIn("'unsafe-eval'", game.headers["Content-Security-Policy"])
        self.assertTrue("no-store" in game.headers["Cache-Control"])

    def test_anonymous_and_missing_asset_have_no_grant_or_model(self):
        anonymous = app.test_client()
        self.assertEqual(anonymous.get(GAME).status_code, 302)
        self.assertEqual(anonymous.get(MODEL).status_code, 302)
        with patch.dict(app.config, {"KIDS_HAND_GAME_ASSET_DIR": "/nonexistent/bad-assets"}):
            self.assertEqual(self.client.get(MODEL).status_code, 404)
            self.assertEqual(self.client.get(GAME + "/assets/NOTICE.txt").status_code, 404)
            self.assertEqual(self.client.post(MODEL, headers=self._csrf_headers(self.client, GAME)).status_code, 405)

    def test_exact_asset_validation_returns_the_same_integrity_checked_bytes(self):
        import hashlib

        content = b"fixture bytes"
        manifest = {"hand_landmarker.task": ("application/octet-stream", len(content), hashlib.sha256(content).hexdigest())}
        with TemporaryDirectory() as directory, patch.object(kids_hand_assets, "ASSETS", manifest):
            path = Path(directory) / "hand_landmarker.task"
            path.write_bytes(content)
            with patch.dict(app.config, {"KIDS_HAND_GAME_ASSET_DIR": directory}):
                page = self.client.get(GAME).get_data(as_text=True)
                self.assertIn('data-ready="yes"', page)
                self.assertIn('data-hand-camera type="button" disabled', page,
                              "A route with valid assets still waits for its JS event handlers")
                result = self.client.get(MODEL)
                self.assertEqual(result.data, content)
                self.assertEqual(result.mimetype, "application/octet-stream")
                self.assertIn("no-store", result.headers["Cache-Control"])
                self.assertIn("camera=()", result.headers["Permissions-Policy"])
                path.write_bytes(b"fixture wrong")
                self.assertIn('data-ready="no"', self.client.get(GAME).get_data(as_text=True))
                self.assertEqual(self.client.get(MODEL).status_code, 404)
