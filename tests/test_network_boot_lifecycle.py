import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g
from app.mini_services_api import bp as control_bp
from app.network_boot_http import bp as http_bp
from app.network_boot_admin import bp as admin_bp
from app.network_boot_service import status
from app.security_controls import protect_browser_mutation
from simpleoffice_network_boot import assets_root, load_boot_settings, save_boot_settings, list_assets, store_asset


class HttpBootLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "mini.json"
        env = patch.dict(os.environ, {"SIMPLEOFFICE_MINI_SERVICES_CONFIG": str(self.path)})
        env.start(); self.addCleanup(env.stop)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test-secret")
        for bp in (control_bp, http_bp, admin_bp):
            self.app.register_blueprint(bp)
        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "Login")
        self.user = {"id": 1, "is_admin": True, "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}
        for target in ("app.mini_services_api.audit", "app.network_boot_admin.audit"):
            mock = patch(target); mock.start(); self.addCleanup(mock.stop)

    def action(self, name):
        return self.client.post("/api/mini-services/http-boot/" + name, json={}, headers=self.headers)

    def test_status_and_idempotent_lifecycle_without_mini_worker(self):
        self.assertEqual("stopped", self.client.get("/api/mini-services/http-boot").json["state"])
        config = {"default_profile": "linux", "profiles": [{"id": "linux", "kernel": "kernel"}]}
        save_boot_settings(config, self.path)
        store_asset(io.BytesIO(b"kernel"), "kernel", self.path)
        for name in ("start", "start", "restart"):
            response = self.action(name)
            self.assertEqual(200, response.status_code)
            self.assertEqual("running", response.json["state"])
        self.assertTrue(load_boot_settings(self.path)["enabled"])
        for name in ("stop", "stop"):
            self.assertEqual("stopped", self.action(name).json["state"])
        self.assertFalse(load_boot_settings(self.path)["enabled"])
        self.assertEqual(404, self.client.get("/network-boot/files/kernel").status_code)

    def test_missing_profile_and_file_have_useful_health_and_recover(self):
        self.assertEqual("waiting", self.action("start").json["state"])
        save_boot_settings({"enabled": True, "default_profile": "linux", "profiles": [{"id": "linux", "kernel": "kernel"}]}, self.path)
        self.assertEqual("degraded", self.client.get("/api/mini-services/http-boot").json["state"])
        store_asset(io.BytesIO(b"kernel"), "kernel", self.path)
        self.assertEqual("running", self.client.get("/api/mini-services/http-boot").json["state"])
        with self.app.app_context(), patch.dict(self.app.blueprints, {}, clear=True):
            self.assertEqual("unavailable", status()["state"])

    def test_scan_is_metadata_only_and_failure_is_persisted(self):
        store_asset(io.BytesIO(b"kernel"), "kernel", self.path)
        with patch("simpleoffice_network_boot.hashlib.sha256", side_effect=AssertionError("no hashing during scan")):
            response = self.action("scan")
        self.assertEqual(1, response.json["count"])
        with patch("app.network_boot_service.list_assets", side_effect=PermissionError):
            self.assertEqual(400, self.action("scan").status_code)
        row = self.client.get("/api/mini-services/http-boot").json
        self.assertEqual("failed", row["scan"]["state"])

    def test_settings_require_admin_and_csrf_and_retain_invalid_submission(self):
        url = "/admin/mini-services/network-boot/settings"
        body = {"boot_json": json.dumps({"enabled": True})}
        self.assertEqual(403, self.client.post(url, data=body).status_code)
        self.user["is_admin"] = False
        self.assertEqual(403, self.client.post(url, data=body, headers=self.headers).status_code)
        self.assertEqual(403, self.action("start").status_code)
        self.user["is_admin"] = True
        self.assertEqual(302, self.client.post(url, data=body, headers=self.headers).status_code)
        self.assertTrue(load_boot_settings(self.path)["enabled"])
        with patch("app.network_boot_admin._page", return_value="invalid") as page:
            response = self.client.post(url, data={"boot_json": '{"enabled":"false"}'}, headers=self.headers)
            self.assertEqual(400, response.status_code)
            page.assert_called_once_with('{"enabled":"false"}')
        self.assertTrue(load_boot_settings(self.path)["enabled"])
        self.assertEqual(302, self.client.post(url, data={"action": "reset"}, headers=self.headers).status_code)
        self.assertFalse(load_boot_settings(self.path)["enabled"])


class BootAssetAtomicityTests(unittest.TestCase):
    def test_parallel_same_name_writes_have_private_distinct_temporaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini.json"

            class InterleavedSource(io.BytesIO):
                def read(self, size=-1):
                    if self.tell() == 0:
                        store_asset(io.BytesIO(b"second"), "kernel", path)
                    return super().read(size)

            store_asset(InterleavedSource(b"first"), "kernel", path)
            target = assets_root(path) / "kernel"
            self.assertEqual(b"first", target.read_bytes())
            self.assertEqual([target], list(assets_root(path).iterdir()))
            if os.name == "posix":
                self.assertEqual(0o600, target.stat().st_mode & 0o777)

    def test_failed_upload_preserves_previous_file_and_scan_skips_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini.json"
            store_asset(io.BytesIO(b"old"), "kernel", path)
            with self.assertRaises(ValueError):
                store_asset(io.BytesIO(b"too large"), "kernel", path, max_bytes=1)
            self.assertEqual(b"old", (assets_root(path) / "kernel").read_bytes())
            (assets_root(path) / ".boot-private.part").write_bytes(b"in progress")
            rows = list_assets(path, include_hash=False)
            self.assertEqual(["kernel"], [row["path"] for row in rows])
            self.assertNotIn("sha256", rows[0])


if __name__ == "__main__":
    unittest.main()
