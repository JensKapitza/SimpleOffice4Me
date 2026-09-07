import os
import unittest

from flask import Flask

from app import app as simpleoffice_app
from app.password_protocol_adapters import adapter_capabilities, require_compatible
from app.password_protocol_http import bp


class PasswordProtocolAdapterTests(unittest.TestCase):
    def setUp(self):
        self.previous_bw = os.environ.get("SIMPLEOFFICE_PASSWORD_PROTOCOL_BITWARDEN")
        self.previous_psono = os.environ.get("SIMPLEOFFICE_PASSWORD_PROTOCOL_PSONO")
        self.app = Flask(__name__)
        self.app.testing = True
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        if self.previous_bw is None:
            os.environ.pop("SIMPLEOFFICE_PASSWORD_PROTOCOL_BITWARDEN", None)
        else:
            os.environ["SIMPLEOFFICE_PASSWORD_PROTOCOL_BITWARDEN"] = self.previous_bw
        if self.previous_psono is None:
            os.environ.pop("SIMPLEOFFICE_PASSWORD_PROTOCOL_PSONO", None)
        else:
            os.environ["SIMPLEOFFICE_PASSWORD_PROTOCOL_PSONO"] = self.previous_psono

    def test_registry_never_claims_experimental_as_compatible(self):
        self.assertEqual("experimental", adapter_capabilities("bitwarden")["status"])
        self.assertFalse(adapter_capabilities("bitwarden")["compatible"])
        self.assertFalse(adapter_capabilities("psono")["compatible"])
        with self.assertRaises(RuntimeError):
            require_compatible("bitwarden")

    def test_adapters_are_registered_in_real_application(self):
        rules = {rule.rule for rule in simpleoffice_app.url_map.iter_rules()}
        self.assertIn("/password-protocols/v1/capabilities", rules)
        self.assertIn("/api/config", rules)
        self.assertIn("/identity/accounts/prelogin", rules)
        self.assertIn("/server/info/", rules)

    def test_adapters_are_disabled_by_default(self):
        os.environ.pop("SIMPLEOFFICE_PASSWORD_PROTOCOL_BITWARDEN", None)
        self.assertEqual(404, self.client.get("/api/config").status_code)
        self.assertEqual(404, self.client.post("/identity/accounts/prelogin", json={"email": "person@example.test"}).status_code)

    def test_bitwarden_discovery_and_prelogin_when_explicitly_enabled(self):
        os.environ["SIMPLEOFFICE_PASSWORD_PROTOCOL_BITWARDEN"] = "1"
        config = self.client.get("/api/config")
        self.assertEqual(200, config.status_code)
        self.assertEqual("experimental", config.get_json()["simpleOfficeCompatibility"]["status"])
        prelogin = self.client.post("/identity/accounts/prelogin", json={"email": "person@example.test"})
        self.assertEqual(200, prelogin.status_code)
        self.assertGreaterEqual(prelogin.get_json()["kdfIterations"], 600000)
        self.assertEqual(501, self.client.post("/identity/connect/token").status_code)
        self.assertEqual(501, self.client.get("/api/sync").status_code)

    def test_psono_discovery_requires_opt_in(self):
        os.environ["SIMPLEOFFICE_PASSWORD_PROTOCOL_PSONO"] = "1"
        response = self.client.get("/server/info/")
        self.assertEqual(200, response.status_code)
        self.assertEqual("experimental", response.get_json()["simpleoffice_compatibility"]["status"])
        self.assertEqual(501, self.client.post("/server/authentication/login/").status_code)


if __name__ == "__main__":
    unittest.main()
