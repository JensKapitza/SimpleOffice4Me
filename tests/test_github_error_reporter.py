import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

from app.github_error_reporter import (
    GitHubReporterConfig,
    LOCAL_REPORT_INFLIGHT_LIMIT,
    LOCAL_REPORT_LIMIT,
    MAX_HTTP_RESPONSE_BYTES,
    _RejectRedirects,
    _local_issue_cache,
    _local_report_attempts,
    _local_report_inflight,
    _request_json,
    _reserve_local_report,
    build_report,
    find_existing_issue,
    load_config,
    manual_issue_url,
    master_error_report_url,
    report_error,
    sanitize_text,
)


class GitHubErrorReporterTests(unittest.TestCase):
    def setUp(self):
        _local_issue_cache.clear()
        _local_report_attempts.clear()
        _local_report_inflight.clear()

    def test_disabled_by_default_in_source_checkout_without_master(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        self.assertFalse(config.enabled)
        self.assertEqual(config.token, "")

    @patch("app.github_error_reporter.master_federation_identity", return_value=("https://master.example.test", False))
    def test_global_master_is_default_error_collector(self, _master):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        self.assertTrue(config.enabled)
        self.assertEqual(config.token, "")
        self.assertEqual(
            "https://master.example.test/api/error-reports/v1/reports",
            config.relay_url,
        )

    @patch("app.github_error_reporter.master_federation_identity", return_value=("https://master.example.test/simpleoffice/", False))
    def test_master_collector_preserves_master_base_path(self, _master):
        self.assertEqual(
            "https://master.example.test/simpleoffice/api/error-reports/v1/reports",
            master_error_report_url(),
        )

    @patch("app.github_error_reporter.master_federation_identity", return_value=("https://master.example.test", True))
    def test_master_build_never_reports_to_itself(self, _master):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        self.assertFalse(config.enabled)
        self.assertEqual("", config.relay_url)

    @patch("app.github_error_reporter.master_federation_identity", return_value=("https://master.example.test", False))
    def test_explicit_relay_overrides_master_collector(self, _master):
        env = {"SIMPLEOFFICE_ERROR_REPORT_URL": "https://custom.example.test/reports"}
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        self.assertEqual(env["SIMPLEOFFICE_ERROR_REPORT_URL"], config.relay_url)

    @patch("app.github_error_reporter.master_federation_identity", return_value=("https://master.example.test", False))
    def test_master_reporting_can_be_explicitly_disabled(self, _master):
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_REPORTING": "0"}, clear=True):
            config = load_config()
        self.assertFalse(config.enabled)
        self.assertEqual("", config.relay_url)

    @patch("app.github_error_reporter.master_federation_identity", return_value=("https://master.example.test", False))
    def test_direct_mode_wins_over_master_fallback(self, _master):
        env = {
            "SIMPLEOFFICE_GITHUB_ERROR_REPORTING": "1",
            "SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY": "JensKapitza/SimpleOffice4Me",
            "SIMPLEOFFICE_GITHUB_ERROR_TOKEN": "github_pat_example",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        self.assertTrue(config.enabled)
        self.assertEqual("", config.relay_url)
        self.assertEqual("github_pat_example", config.token)

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

    def test_sanitizer_bounds_work_on_oversized_error_text(self):
        cleaned = sanitize_text("A" * 100_000 + " password=too-late", 100)
        self.assertEqual(100, len(cleaned))

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

    def test_manual_issue_url_rejects_invalid_repository(self):
        with self.assertRaises(ValueError):
            manual_issue_url("0123456789abcdef", "../attacker/repository")

    def test_token_file_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "token"
            target.write_text("github_pat_secret", encoding="utf-8")
            target.chmod(0o600)
            link = root / "token-link"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            env = {
                "SIMPLEOFFICE_GITHUB_ERROR_REPORTING": "1",
                "SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE": str(link),
            }
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    load_config()

    def test_oversized_token_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token"
            token_file.write_text("x" * 5000, encoding="utf-8")
            token_file.chmod(0o600)
            env = {
                "SIMPLEOFFICE_GITHUB_ERROR_REPORTING": "1",
                "SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE": str(token_file),
            }
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    load_config()

    @patch("app.github_error_reporter.urllib.request.build_opener")
    def test_github_request_uses_redirect_rejecting_opener(self, build_opener):
        opener = MagicMock()
        response = MagicMock()
        response.read.return_value = b"{}"
        opener.open.return_value.__enter__.return_value = response
        build_opener.return_value = opener
        config = GitHubReporterConfig(True, "JensKapitza/SimpleOffice4Me", "token")

        self.assertEqual({}, _request_json(config, "GET", "/rate_limit"))
        handler = build_opener.call_args.args[0]
        self.assertIsInstance(handler, _RejectRedirects)
        request = opener.open.call_args.args[0]
        self.assertEqual("Bearer token", request.get_header("Authorization"))

    @patch("app.github_error_reporter.urllib.request.build_opener")
    def test_http_response_size_is_bounded(self, build_opener):
        opener = MagicMock()
        response = MagicMock()
        response.read.return_value = b"x" * (MAX_HTTP_RESPONSE_BYTES + 1)
        opener.open.return_value.__enter__.return_value = response
        build_opener.return_value = opener
        config = GitHubReporterConfig(True, "JensKapitza/SimpleOffice4Me", "token")

        with self.assertRaises(RuntimeError):
            _request_json(config, "GET", "/rate_limit")

    @patch("app.github_error_reporter._request_json")
    def test_existing_issue_is_reused(self, request_json):
        request_json.return_value = {"items": [{"number": 123}]}
        config = GitHubReporterConfig(True, "JensKapitza/SimpleOffice4Me", "token")
        self.assertEqual(find_existing_issue(config, "fp123"), 123)
        path = request_json.call_args.args[2]
        self.assertIn("search/issues", path)
        self.assertIn("fp123", path)
        self.assertIn("per_page=1", path)

    @patch("app.github_error_reporter._post_relay", return_value={"issue_number": 321})
    @patch("app.github_error_reporter.load_config")
    def test_successful_report_is_cached_locally(self, load_config_mock, post_relay):
        load_config_mock.return_value = GitHubReporterConfig(
            True,
            "JensKapitza/SimpleOffice4Me",
            "",
            relay_url="https://errors.example.test/api/error-reports/v1/reports",
        )
        kwargs = {
            "exception_type": "UndefinedError",
            "exception_message": "ignored",
            "endpoint": "contacts.merge",
            "method": "POST",
            "request_id": "abcd1234",
            "fingerprint": "abcdef1234567890abcd",
            "frames": [],
        }
        self.assertEqual(321, report_error(**kwargs))
        self.assertEqual(321, report_error(**kwargs))
        self.assertEqual(1, post_relay.call_count)

    @patch("app.github_error_reporter.time.monotonic", return_value=100.0)
    def test_local_report_budget_is_bounded(self, _clock):
        _local_report_attempts.extend([100.0] * LOCAL_REPORT_LIMIT)
        status, issue = _reserve_local_report("abcdef1234567890abcd")
        self.assertEqual("limited", status)
        self.assertIsNone(issue)
        self.assertNotIn("abcdef1234567890abcd", _local_report_inflight)

    def test_local_report_parallelism_is_bounded(self):
        _local_report_inflight.update(
            f"busy-{index}" for index in range(LOCAL_REPORT_INFLIGHT_LIMIT)
        )
        status, issue = _reserve_local_report("abcdef1234567890abcd")
        self.assertEqual("busy", status)
        self.assertIsNone(issue)


if __name__ == "__main__":
    unittest.main()
