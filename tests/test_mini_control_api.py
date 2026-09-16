import tempfile
import time
import unittest
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g
from app.mini_services_api import bp
from app.security_controls import protect_browser_mutation
from simpleoffice_mini_control import ControlStore
from simpleoffice_mini_core import write_status
from tools.mini_services import Worker


class ControlStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mini.json"
        self.store = ControlStore(self.path)

    def test_settings_validate_persist_and_cannot_create_services(self):
        for value in ({"enabled": "false", "autostart": True}, {}, None):
            with self.assertRaises(ValueError):
                self.store.save_preferences("sip", value)
        self.store.save_preferences("sip", {"enabled": True, "autostart": False})
        self.assertFalse(ControlStore(self.path).preferences()["sip"]["autostart"])
        with self.assertRaises(ValueError):
            self.store.enqueue("../../outside", "start")

    def test_duplicate_pending_actions_and_stop_start_order(self):
        first = self.store.enqueue("sip", "start")
        self.assertEqual(first["id"], self.store.enqueue("sip", "start")["id"])
        stop = self.store.enqueue("sip", "stop")
        second = self.store.enqueue("sip", "start")
        self.assertNotEqual(first["id"], second["id"])
        for expected in (first, stop, second):
            command = self.store.claim()
            self.assertEqual(expected["id"], command["id"])
            self.store.finish(command["id"], True, {"ok": True})
        self.assertIsNone(self.store.claim())

    def test_queue_is_bounded_and_expired_commands_do_not_run(self):
        for i in range(32):
            self.store.enqueue("sip", "start" if i % 2 else "stop")
        with self.assertRaises(ValueError):
            self.store.enqueue("dns", "start")
        with patch("simpleoffice_mini_control.time.time", return_value=time.time() + 120):
            self.assertIsNone(self.store.claim())

    def test_crash_recovery_marks_unfinished_actions_as_failed(self):
        action = self.store.enqueue("sip", "start")
        self.store.claim()
        self.store.recover_interrupted()
        self.assertEqual("failed", self.store.operation(action["id"])["state"])

    def test_worker_manual_stop_and_start_preserve_autostart_preference(self):
        self.store.save_preferences("sip", {"enabled": True, "autostart": False})
        worker = Worker(self.path)
        with patch("tools.mini_services.SipRegistrarService") as sip:
            worker._load_network_services()
            sip.assert_not_called()
            for action in ("start", "stop", "restart"):
                command = self.store.enqueue("sip", action)
                worker._execute(self.store.claim())
                self.assertEqual("completed", self.store.operation(command["id"])["state"])
            worker.stop()
        self.assertFalse(self.store.preferences()["sip"]["autostart"])


class MiniApiTests(unittest.TestCase):
    def test_windows_receiver_scan_reports_unverified_default_without_registering_sink(self):
        with patch("app.audio_output_discovery.platform.system", return_value="Windows"), patch("app.audio_output_discovery.shutil.which", return_value="ffplay"), patch("app.audio_output_admin._store") as output_store:
            result = self.client.post("/api/mini-services/audio-receiver/scan", json={}, headers=self.headers)
            self.assertEqual(200, result.status_code)
            self.assertEqual("default", result.json["targets"][0]["id"])
            self.assertEqual("unknown", result.json["targets"][0]["state"])
            self.assertIn("Hardware nicht geprüft", result.json["scope"])
            output_store.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mini.json"
        self.user = {"id": 1, "is_admin": True, "is_disabled": False}
        environment = patch.dict(os.environ, {"SIMPLEOFFICE_MINI_SERVICES_CONFIG": str(self.path)})
        environment.start(); self.addCleanup(environment.stop)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test-secret")
        self.app.register_blueprint(bp)
        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "Login")
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}
        patcher = patch("app.mini_services_api.default_config_path", return_value=self.path)
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch("app.mini_services_api.audit")
        patcher.start(); self.addCleanup(patcher.stop)

    def test_auth_admin_and_csrf_are_required(self):
        self.user = None
        self.assertEqual(302, self.client.get("/api/mini-services").status_code)
        self.user = {"is_admin": False, "is_disabled": False}
        self.assertEqual(403, self.client.get("/api/mini-services").status_code)
        self.user["is_admin"] = True
        self.assertEqual(403, self.client.post("/api/mini-services/sip/start", json={}).status_code)

    def test_missing_worker_returns_actionable_503(self):
        response = self.client.post("/api/mini-services/sip/start", json={}, headers=self.headers)
        self.assertEqual(503, response.status_code)
        self.assertEqual("worker_unavailable", response.json["code"])

    def test_api_queues_and_reports_worker_result(self):
        write_status({"state": "running"}, self.path)
        response = self.client.post("/api/mini-services/sip/start", json={}, headers=self.headers)
        self.assertEqual(202, response.status_code)
        store = ControlStore(self.path)
        command = store.claim()
        store.finish(command["id"], True, {"state": "running"})
        result = self.client.get("/api/mini-services/operations/" + response.json["id"])
        self.assertEqual("completed", result.json["state"])
        self.assertEqual("no-store", result.headers["Cache-Control"])

    def test_scan_reports_count_time_and_interfaces(self):
        with patch("app.network_system_status.network_interfaces", return_value=[{"name": "eth0", "ipv4": ["192.168.2.1/24"]}]):
            response = self.client.post("/api/mini-services/dhcp/scan", json={}, headers=self.headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json["count"])
        self.assertEqual("completed", self.client.get("/api/mini-services/dhcp").json["scan"]["state"])

    def test_unknown_actions_and_settings_are_rejected(self):
        self.assertEqual(404, self.client.post("/api/mini-services/sip/shell", json={}, headers=self.headers).status_code)
        self.assertEqual(400, self.client.post("/api/mini-services/sip/settings", json={"enabled": "false"}, headers=self.headers).status_code)

    def test_audio_storage_failure_does_not_hide_network_and_boot_status(self):
        with patch("app.audio_streamer_config.settings", side_effect=sqlite3.OperationalError("sensitive")), patch("app.audio_output_worker.worker.settings", side_effect=sqlite3.OperationalError("sensitive")):
            response = self.client.get("/api/mini-services")
        self.assertEqual(200, response.status_code)
        states = {row["id"]: row["state"] for row in response.json["services"]}
        self.assertEqual("degraded", states["audio-sender"])
        self.assertEqual("degraded", states["audio-output"])
        self.assertIn("http-boot", states)
        self.assertIn("dhcp", states)
        self.assertNotIn("sensitive", response.get_data(as_text=True))

    def test_common_catalog_includes_existing_audio_owners(self):
        result = self.client.get("/api/mini-services")
        self.assertEqual(200, result.status_code)
        services = {row["id"]: row for row in result.json["services"]}
        for name in ("audio-sender", "audio-receiver", "audio-output"):
            self.assertEqual("web", services[name]["owner"])
            self.assertIn("health", services[name])


if __name__ == "__main__":
    unittest.main()
