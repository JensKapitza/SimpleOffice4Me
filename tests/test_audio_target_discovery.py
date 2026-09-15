import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, g
from app.audio_target_discovery import receiver_capability, targets_from_profiles
from app.audio_streamer_admin import bp, _target_scan_lock
from app.federation_discovery_http import bp as discovery_bp
from app.security_controls import protect_browser_mutation


class AudioTargetDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test", TEST_CSRF_PROTECTION=True,
                               DOCUMENT_ROOT=self.temporary.name)
        self.app.register_blueprint(bp)
        self.app.register_blueprint(discovery_bp)
        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "Login")
        self.user = {"id": 1, "is_admin": True, "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}

    def profile(self, **capability):
        return {"base_url": "http://192.168.1.20:8080", "label": "Receiver",
                "capabilities": {"audio_receiver": {"host": "192.168.1.20", "port": 5004,
                    "codec": "opus", "transport": "rtp-udp", **capability}}}

    def test_only_live_private_receivers_are_advertised(self):
        receiver = {"running": True, "bind": "192.168.1.20", "port": 5004}
        self.assertEqual(5004, receiver_capability({"receiver": receiver})["port"])
        for changes in ({"running": False}, {"bind": "127.0.0.1"}, {"bind": "0.0.0.0"}, {"port": 65535}):
            self.assertIsNone(receiver_capability({"receiver": {**receiver, **changes}}))

    def test_untrusted_profiles_cannot_redirect_microphone_to_third_party(self):
        valid = self.profile()
        invalid = [self.profile(host="8.8.8.8"), self.profile(host="192.168.1.30"),
                   self.profile(port=True), self.profile(codec="unknown"), {}, None]
        result = targets_from_profiles([valid, valid, *invalid])
        self.assertEqual(["192.168.1.20:5004"], [target["id"] for target in result])

    def test_scan_permissions_results_and_concurrent_request(self):
        url = "/admin/mini-services/audio/streamer/targets/scan"
        with patch("app.federation_discovery_lan.discover_lan", return_value={"peers": [self.profile()], "networks": ["192.168.1.0/24"]}) as scan:
            self.assertEqual(403, self.client.post(url).status_code)
            self.user["is_admin"] = False
            self.assertEqual(403, self.client.post(url, headers=self.headers).status_code)
            scan.assert_not_called()
            self.user["is_admin"] = True
            response = self.client.post(url, headers=self.headers)
            self.assertEqual(1, response.json["count"])
            self.assertEqual("no-store", response.headers["Cache-Control"])
            with _target_scan_lock:
                self.assertEqual(409, self.client.post(url, headers=self.headers).status_code)
            self.assertEqual(1, scan.call_count)

    def test_failed_scan_releases_lock_and_has_safe_error(self):
        with patch("app.federation_discovery_lan.discover_lan", side_effect=OSError("secret")), patch("app.audio_streamer_admin.audit"):
            response = self.client.post("/admin/mini-services/audio/streamer/targets/scan", headers=self.headers)
        self.assertEqual(503, response.status_code)
        self.assertNotIn("secret", response.get_data(as_text=True))
        self.assertFalse(_target_scan_lock.locked())

    def test_public_profile_does_not_expose_receiver_and_stop_removes_it(self):
        receiver = {"receiver": {"running": True, "bind": "192.168.1.20", "port": 5004}}
        with patch("app.federation_discovery_http.local_profile", side_effect=lambda *a, **kw: {"capabilities": {}}), patch("app.audio_streamer.manager.status", return_value=receiver):
            url = "/.well-known/simpleoffice-federation"
            public = self.client.get(url, environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
            self.assertNotIn("audio_receiver", public.json["capabilities"])
            local = self.client.get(url, environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
            self.assertIn("audio_receiver", local.json["capabilities"])
            receiver["receiver"]["running"] = False
            stopped = self.client.get(url, environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
            self.assertNotIn("audio_receiver", stopped.json["capabilities"])
