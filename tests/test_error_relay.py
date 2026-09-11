import os
import unittest
from unittest.mock import patch

from app import app
from app.error_relay import _issue_cache, validate_report_payload
from app.github_error_reporter import GitHubReporterConfig


class ErrorRelayTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True)
        _issue_cache.clear()
        self.client = app.test_client()
        self.payload = {
            "schema": 1,
            "request_id": "0123456789abcdef",
            "fingerprint": "abcdef1234567890abcd",
            "exception_type": "UndefinedError",
            "endpoint": "contacts.merge",
            "method": "POST",
            "app_version": "2026.09.11",
            "frames": [{"file": "contacts.py", "line": 42, "function": "merge_contacts"}],
        }

    def test_strict_allowlist_rejects_extra_fields(self):
        payload = dict(self.payload)
        payload["password"] = "must-never-pass"
        with self.assertRaises(ValueError):
            validate_report_payload(payload)

    def test_markdown_injection_in_frame_is_rejected(self):
        payload = dict(self.payload)
        payload["frames"] = [{
            "file": "contacts.py",
            "line": 42,
            "function": "merge```\n[click](https://evil.example)",
        }]
        with self.assertRaises(ValueError):
            validate_report_payload(payload)

    def test_relay_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/error-reports/v1/reports", json=self.payload)
        self.assertEqual(404, response.status_code)

    @patch("app.error_relay.load_config")
    def test_health_is_unavailable_without_github_credential(self, load_config):
        load_config.return_value = GitHubReporterConfig(True, "JensKapitza/SimpleOffice4Me", "")
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_RELAY_ENABLED": "1"}, clear=True):
            response = self.client.get("/api/error-reports/v1/health")
        self.assertEqual(503, response.status_code)
        self.assertFalse(response.get_json()["ready"])

    @patch("app.error_relay.load_config")
    def test_health_is_ready_with_github_credential(self, load_config):
        load_config.return_value = GitHubReporterConfig(True, "JensKapitza/SimpleOffice4Me", "relay-token")
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_RELAY_ENABLED": "1"}, clear=True):
            response = self.client.get("/api/error-reports/v1/health")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ready"])

    @patch("app.error_relay.report_payload_to_github", return_value=321)
    @patch("app.error_relay.load_config")
    def test_valid_anonymous_report_is_forwarded_without_federation(self, load_config, report):
        load_config.return_value = GitHubReporterConfig(
            enabled=True,
            repository="JensKapitza/SimpleOffice4Me",
            token="relay-only-token",
        )
        env = {"SIMPLEOFFICE_ERROR_RELAY_ENABLED": "1"}
        with patch.dict(os.environ, env, clear=True):
            response = self.client.post("/api/error-reports/v1/reports", json=self.payload)
        self.assertEqual(202, response.status_code)
        self.assertEqual(321, response.get_json()["issue_number"])
        forwarded = report.call_args.args[1]
        self.assertEqual(set(self.payload), set(forwarded))
        self.assertNotIn("password", forwarded)
        self.assertNotIn("cookie", forwarded)
        self.assertNotIn("authorization", forwarded)

    @patch("app.error_relay.report_payload_to_github")
    @patch("app.error_relay.load_config")
    def test_invalid_report_never_reaches_github(self, load_config, report):
        load_config.return_value = GitHubReporterConfig(True, "JensKapitza/SimpleOffice4Me", "token")
        payload = dict(self.payload)
        payload["request_body"] = "customer secret"
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_RELAY_ENABLED": "1"}, clear=True):
            response = self.client.post("/api/error-reports/v1/reports", json=payload)
        self.assertEqual(400, response.status_code)
        report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
