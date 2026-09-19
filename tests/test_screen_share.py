import unittest
import shutil
import subprocess
from pathlib import Path
from unittest import mock
from flask import Flask, g

from app import app
from app import screen_share
from app.security_controls import protect_browser_mutation


ROOT = Path(__file__).resolve().parents[1]


class ScreenSignalingTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test")
        self.app.register_blueprint(screen_share.bp)
        self.user = {"id": "owner", "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}
        screen_share._sessions.clear()
        self.addCleanup(screen_share._sessions.clear)
        patcher = mock.patch.object(screen_share, "audit")
        patcher.start(); self.addCleanup(patcher.stop)
        self.session = self.create().json["session"]
        self.url = "/screen/api/sessions/" + self.session["session_id"] + "/signals"

    def create(self):
        return self.client.post("/screen/api/sessions", headers=self.headers)

    def signal(self, kind="offer", payload=None, role="sender", code=""):
        return self.client.post(self.url, json={"role": role, "code": code, "type": kind, "payload": payload}, headers=self.headers)

    def test_invalid_json_payload_role_and_size_are_rejected_without_mutation(self):
        for body in ([1], "text", None, 5):
            response = self.client.post(self.url, json=body, headers=self.headers)
            self.assertIn(response.status_code, (400, 415))
        for kind, payload, role in (("offer", {}, "sender"), ("offer", {"type": "offer", "sdp": "v=0"}, "receiver"), ("ice", {"candidate": 3}, "sender"), ("ice", {"candidate": "x", "sdpMLineIndex": True}, "sender"), ("bye", {}, "sender")):
            self.assertEqual(400, self.signal(kind, payload, role).status_code)
        self.assertEqual(413, self.signal(payload={"type": "offer", "sdp": "x" * 128000}).status_code)
        self.assertEqual(0, screen_share._sessions[self.session["session_id"]]["sequence"])

    def test_offer_answer_ice_permissions_and_csrf(self):
        self.assertEqual(201, self.create().status_code)
        self.assertEqual(403, self.client.post(self.url, json={}).status_code)
        offer = {"type": "offer", "sdp": "v=0"}
        self.assertEqual(200, self.signal(payload=offer).status_code)
        self.user["id"] = "receiver"
        self.assertEqual(403, self.signal(payload=offer).status_code)
        self.assertEqual(403, self.signal("answer", {"type": "answer", "sdp": "v=0"}, "receiver", "wrong").status_code)
        self.assertEqual(200, self.signal("answer", {"type": "answer", "sdp": "v=0"}, "receiver", self.session["join_code"]).status_code)
        response = self.client.get(self.url, query_string={"role": "receiver"}, headers={"X-Screen-Code": self.session["join_code"]})
        self.assertEqual(["offer"], [item["type"] for item in response.json["messages"]])
        self.assertEqual(200, self.signal("ice", {"candidate": "candidate:1", "sdpMid": "0", "sdpMLineIndex": 0}, "receiver", self.session["join_code"]).status_code)

    def test_join_code_is_submitted_in_body_or_header_instead_of_url(self):
        code = self.session["join_code"]
        self.assertEqual(403, self.client.post("/screen/api/join", json={"code": code}).status_code)
        result = self.client.post("/screen/api/join", json={"code": code}, headers=self.headers)
        self.assertEqual(self.session, result.json["session"])
        self.assertEqual(404, self.client.get("/screen/api/join/" + code).status_code)
        self.assertEqual(403, self.client.get(self.url, query_string={"role": "receiver", "code": code}).status_code)
        self.user["id"] = "receiver"
        close_url = self.url.removesuffix("/signals")
        self.assertEqual(403, self.client.delete(close_url, query_string={"code": code}, headers=self.headers).status_code)
        self.assertEqual(200, self.client.delete(close_url, headers={**self.headers, "X-Screen-Code": code}).status_code)

    def test_session_quotas_and_closed_or_expired_slots_are_reclaimed(self):
        with mock.patch.object(screen_share, "_MAX_SESSIONS_PER_USER", 1):
            self.assertEqual(429, self.create().status_code)
        with mock.patch.object(screen_share, "_MAX_SESSIONS", 1):
            self.user["id"] = "another"
            self.assertEqual(429, self.create().status_code)
        self.user["id"] = "owner"
        self.assertEqual(200, self.signal("bye").status_code)
        with mock.patch.object(screen_share, "_MAX_SESSIONS", 1):
            self.assertEqual(201, self.create().status_code)
            for value in screen_share._sessions.values():
                value["updated_at"] = 0
            self.assertEqual(201, self.create().status_code)

    def test_signal_limit_preserves_offer_and_stop_remains_possible(self):
        self.assertEqual(200, self.signal(payload={"type": "offer", "sdp": "v=0"}).status_code)
        for name in ("_MAX_SIGNAL_MESSAGES", "_MAX_SESSION_SIGNAL_BYTES"):
            with mock.patch.object(screen_share, name, 1):
                self.assertEqual(409, self.signal("ice", {"candidate": "x"}).status_code)
        messages = screen_share._sessions[self.session["session_id"]]["messages"]
        self.assertEqual(["offer"], [item["type"] for item in messages])
        self.assertEqual(200, self.signal("bye").status_code)
        self.assertEqual(404, self.client.get(self.url, query_string={"role": "sender"}).status_code)

    def test_authenticated_poll_keeps_active_session_alive(self):
        value = screen_share._sessions[self.session["session_id"]]
        previous = value["updated_at"] = screen_share.time.time() - 100
        self.assertEqual(200, self.client.get(self.url, query_string={"role": "sender"}).status_code)
        self.assertGreater(value["updated_at"], previous)

    def test_connection_qr_is_owner_only_and_contains_no_store_header(self):
        path = "/screen/api/sessions/" + self.session["session_id"] + "/connect-qr.svg"
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertIn("image/svg+xml", response.content_type)
        self.user["id"] = "receiver"
        self.assertEqual(403, self.client.get(path).status_code)


class ScreenShareTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for frontend regression tests")
    def test_frontend_runtime_cleanup_and_recovery(self):
        result = subprocess.run([shutil.which("node"), "--test", str(ROOT / "tests/screen_share_frontend.test.cjs")], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_blueprint_and_navigation_are_registered(self):
        self.assertIn("screen", app.blueprints)
        self.assertIn('url_for(\'screen.index\')', (ROOT / "templates/documents/nav.html").read_text(encoding="utf-8"))

    def test_screen_capture_policy_is_scoped_to_screen_blueprint(self):
        response = app.test_client().get("/screen")
        self.assertIn("display-capture=(self)", response.headers["Permissions-Policy"])
        response = app.test_client().get("/")
        self.assertIn("display-capture=()", response.headers["Permissions-Policy"])

    def test_linux_launchers_use_argument_arrays_without_shell_or_root(self):
        with mock.patch.object(screen_share, "_system", return_value="linux"), \
                mock.patch.object(screen_share.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
                mock.patch.object(screen_share.subprocess, "Popen") as popen:
            screen_share._perform_action("linux-send")
            self.assertEqual(["/usr/bin/gnome-network-displays"], popen.call_args.args[0])
            self.assertNotIn("shell", popen.call_args.kwargs)
            screen_share._perform_action("linux-receive")
            command = popen.call_args.args[0]
            self.assertIn("/usr/bin/miracle-sinkctl", command)
            self.assertNotIn("sudo", command)

    def test_frontend_has_native_and_browser_capture_paths(self):
        source = (ROOT / "static/js/screen_share.js").read_text(encoding="utf-8")
        self.assertIn("SimpleOfficeNativeScreen?.startShare", source)
        self.assertIn("navigator.mediaDevices?.getDisplayMedia", source)
        self.assertIn("pendingCandidates", source)

    def test_electron_ipc_handlers_are_present_and_validated(self):
        source = (ROOT / "desktop/electron/main.js").read_text(encoding="utf-8")
        self.assertIn("ipcMain.handle('screen:status'", source)
        self.assertIn("ipcMain.handle('screen:action'", source)
        self.assertIn("trustedIpcSender", source)
        self.assertIn("shell: false", source)


if __name__ == "__main__":
    unittest.main()
