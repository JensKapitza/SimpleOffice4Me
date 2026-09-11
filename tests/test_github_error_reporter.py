import os
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from app.github_error_reporter import (
    GitHubReporterConfig,
    build_report,
    find_existing_issue,
    load_config,
    manual_issue_url,
    sanitize_text,
)


class GitHubErrorReporterTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        self.assertFalse(config.enabled)
        self.assertEqual(config.token, "")

    def test_token_is_read_from_environment_only_for_direct_mode(self):
        env = {
            "SIMPLEOFFICE_GITHUB_ERROR_REPORTING": "1",
            "SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY": "JensKapitza/SimpleOffice4Me",
            "SIMPLEOFFICE_GITHUB_ERROR_TOKEN": "github_pat_example",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        self.assertTrue(config.enabled)
        self.assertEqual(config.repository, "JensKapitza/SimpleOffice4Me")
        self.assertEqual(config.token, "github_pat_example")

    def test_relay_mode_needs_no_local_github_token(self):
        env = {
            "SIMPLEOFFICE_ERROR_REPORT_URL": "https://errors.example.test/api/error-reports/v1/reports",
            "SIMPLEOFFICE_GITHUB_ERROR_TOKEN": "must-not-be-read",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        self.assertTrue(config.enabled)
        self.assertEqual(config.token, "")
        self.assertEqual(config.relay_url, env["SIMPLEOFFICE_ERROR_REPORT_URL"])

    def test_sanitizer_removes_common_secrets_and_email(self):
        value = "password=hunter2 token=abc123 person@example.test /srv/private/customer.txt"
        cleaned = sanitize_text(value)
        self.assertNotIn("hunter2", cleaned)
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("person@example.test", cleaned)
        self.assertNotIn("/srv/private", cleaned)

    def test_report_has_strict_allowlist(self):
        title, body = build_report(
            exception_type="UndefinedError",
            exception_message="password=secret alice@example.test",
            endpoint="contacts.merge",
            method="POST",
            request_id="abcd1234",
            fingerprint="fp123",
            frames=[{"file": "/srv/app/contacts.py", "line": 42, "function": "merge"}],
            app_version="test",
        )
        self.assertIn("UndefinedError", title)
        self.assertIn("contacts.merge", body)
        self.assertIn("fp123", body)
        self.assertNotIn("secret", body)
        self.assertNotIn("alice@example.test", body)
        self.assertNotIn("/srv/app", body)
        self.assertNotIn("cookie", body.casefold())
        self.assertNotIn("authorization", body.casefold())

    def test_manual_issue_url_contains_safe_diagnostics_without_secrets(self):
        url = manual_issue_url(
            "0123456789abcdef",
            exception_type="UndefinedError",
            endpoint="contacts.merge",
            method="POST",
            fingerprint="abcdef1234567890abcd",
            frames=[{"file": "/srv/private/contacts.py", "line": 42, "function": "merge_contacts"}],
            app_version="2026.09.11",
        )
        self.assertTrue(url.startswith("https://github.com/JensKapitza/SimpleOffice4Me/issues/new?"))
        params = parse_qs(urlsplit(url).query)
        body = params["body"][0]
        title = params["title"][0]
        self.assertIn("UndefinedError", title)
        self.assertIn("contacts.merge", title)
        self.assertIn("0123456789abcdef", body)
        self.assertIn("abcdef1234567890abcd", body)
        self.assertIn("contacts.py", body)
        self.assertNotIn("/srv/private", body)
        self.assertNotIn("github_pat_", url.casefold())
        self.assertNotIn("authorization%3a", url.casefold())

    @patch("app.github_error_reporter._request_json")
    def test_existing_issue_is_reused(self, request_json):
        request_json.return_value = {"items": [{"number": 123}]}
        config = GitHubReporterConfig(True, "JensKapitza/SimpleOffice4Me", "token")
        self.assertEqual(find_existing_issue(config, "fp123"), 123)
        path = request_json.call_args.args[2]
        self.assertIn("search/issues", path)
        self.assertIn("fp123", path)


if __name__ == "__main__":
    unittest.main()
