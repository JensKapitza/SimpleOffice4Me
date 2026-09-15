import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g
from app.mini_services_admin import bp
from app.security_controls import protect_browser_mutation
from simpleoffice_mini_core import load_config, save_config
from simpleoffice_network_gateway_runtime import load_gateway_settings, save_gateway_settings
from tools.mini_services import Worker


class NetworkSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "mini.json"
        env = patch.dict(os.environ, {"SIMPLEOFFICE_MINI_SERVICES_CONFIG": str(self.path)})
        env.start(); self.addCleanup(env.stop)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test")
        self.app.register_blueprint(bp)
        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "Login")
        self.user = {"id": 1, "is_admin": True, "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}
        audit = patch("app.mini_services_admin.audit")
        audit.start(); self.addCleanup(audit.stop)

    def test_gateway_configuration_requires_admin_and_csrf_and_persists(self):
        url = "/admin/mini-services/gateway/settings"
        body = {"mode": "nat", "enabled": "1", "auto_detect": "1", "internal_network": "10.20.0.0/24"}
        self.assertEqual(403, self.client.post(url, data=body).status_code)
        self.user["is_admin"] = False
        self.assertEqual(403, self.client.post(url, data=body, headers=self.headers).status_code)
        self.user["is_admin"] = True
        self.assertEqual(302, self.client.post(url, data=body, headers=self.headers).status_code)
        self.assertTrue(load_gateway_settings(self.path)["enabled"])
        self.assertEqual("10.20.0.0/24", load_gateway_settings(self.path)["internal_network"])
        with patch("app.mini_services_admin._network_context", return_value={}), patch("app.mini_services_admin.render_template", return_value="invalid") as render:
            self.assertEqual(400, self.client.post(url, data={**body, "internal_network": "invalid"}, headers=self.headers).status_code)
            self.assertEqual("invalid", render.call_args.kwargs["gateway"]["internal_network"])
        self.assertEqual("10.20.0.0/24", load_gateway_settings(self.path)["internal_network"])
        self.client.post(url, data={"action": "reset"}, headers=self.headers)
        self.assertFalse(load_gateway_settings(self.path)["enabled"])

    def test_dns_reset_preserves_dhcp_config_and_requires_csrf(self):
        save_config({"dhcp": {"port": 1067}, "dns": {"enabled": True, "port": 1053}}, self.path)
        url = "/admin/mini-services/network/dns/reset"
        self.assertEqual(403, self.client.post(url).status_code)
        self.assertEqual(302, self.client.post(url, headers=self.headers).status_code)
        config = load_config(self.path)
        self.assertFalse(config["dns"]["enabled"])
        self.assertEqual(53, config["dns"]["port"])
        self.assertEqual(1067, config["dhcp"]["port"])
        self.assertEqual(404, self.client.post("/admin/mini-services/network/unknown/reset", headers=self.headers).status_code)

    def test_gateway_network_is_independent_when_dhcp_is_disabled(self):
        save_gateway_settings({"internal_network": "10.20.0.0/24"}, self.path)
        worker = Worker(self.path)
        with patch("tools.mini_services.SipRegistrarService"):
            worker._load_network_services()
            self.assertEqual("10.20.0.0/24", worker.desired["gateway"][1]["internal_network"])
            worker.stop()


if __name__ == "__main__":
    unittest.main()
