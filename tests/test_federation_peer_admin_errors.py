import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, g

from app.federation_peer_admin import bp
from app.security_controls import protect_browser_mutation


class FederationPeerAdminErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            SECRET_KEY="test",
            DOCUMENT_ROOT=str(Path(temporary.name) / "documents"),
        )
        self.app.register_blueprint(bp)
        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "Login")
        self.user = {"id": 1, "username": "admin", "is_admin": True, "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}

    def _latest_flash(self):
        with self.client.session_transaction() as session:
            flashes = session.get("_flashes", [])
            return flashes[-1][1] if flashes else ""

    def test_receive_start_storage_failure_redirects_without_500_or_exception_text(self):
        state = Mock()
        state.start.side_effect = PermissionError("secret-device-path")
        with patch("app.federation_peer_admin._receive_state", return_value=state):
            response = self.client.post(
                "/admin/federation/peer-discovery/receive/start",
                headers=self.headers,
            )
        self.assertEqual(302, response.status_code)
        message = self._latest_flash()
        self.assertIn("konnte nicht aktiviert werden", message)
        self.assertNotIn("secret-device-path", message)

    def test_receive_stop_storage_failure_redirects_without_500_or_exception_text(self):
        state = Mock()
        state.stop.side_effect = OSError("secret-device-path")
        with patch("app.federation_peer_admin._receive_state", return_value=state):
            response = self.client.post(
                "/admin/federation/peer-discovery/receive/stop",
                headers=self.headers,
            )
        self.assertEqual(302, response.status_code)
        message = self._latest_flash()
        self.assertIn("konnte nicht beendet werden", message)
        self.assertNotIn("secret-device-path", message)


if __name__ == "__main__":
    unittest.main()
