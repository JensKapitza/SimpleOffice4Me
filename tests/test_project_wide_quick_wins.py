import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.access_control import _redact_detail, has_feature
from app.applogging import redact
from app.bs4 import renderwithbs4
from app.datalogger_collectors import CollectionError, _safe_file_path, collect_file, collect_http
from app.ics_preview import MAX_UNFOLDED_LINE_CHARS, preview_ics
from app.revision_history import _path_component
from app.system_identity import _read_installation_id
from tools.service_control import _role, process_matches


class ProjectWideQuickWinsTest(unittest.TestCase):
    def test_legacy_render_helper_returns_jinja_output_without_html_reparse(self):
        markup = "<!doctype html><html><body><input disabled></body></html>"
        with patch("app.bs4.render_template", return_value=markup) as render:
            self.assertEqual(markup, renderwithbs4("dashboard"))
        render.assert_called_once_with("dashboard.html")

    def test_log_redaction_covers_json_query_and_url_credentials(self):
        value = (
            'client_secret=one https://alice:two@example.test/path?access_token=three '
            '{"refresh_token":"four"}'
        )
        cleaned = redact(value)
        for secret in ("one", "two", "three", "four"):
            self.assertNotIn(secret, cleaned)
        self.assertIn("alice:[REDACTED]@example.test", cleaned)

    def test_audit_detail_redaction_is_recursive_and_bounded(self):
        value = {
            "normal": "visible",
            "credentials": {"password": "hidden", "nested": [{"access_token": "also-hidden"}]},
            "payload": b"abc",
        }
        safe = _redact_detail(value)
        self.assertEqual("visible", safe["normal"])
        self.assertEqual("[REDACTED]", safe["credentials"])
        self.assertEqual("[BYTES:3]", safe["payload"])
        self.assertNotIn("hidden", json.dumps(safe))

    def test_unknown_feature_and_missing_user_fail_closed(self):
        self.assertFalse(has_feature(None, "documents"))
        self.assertFalse(has_feature({"is_disabled": 0, "is_admin": 1}, "does-not-exist"))
        self.assertFalse(has_feature({"is_disabled": 1, "is_admin": 1}, "documents"))

    def test_revision_snapshot_component_cannot_traverse(self):
        value = _path_component("../../outside/private")
        self.assertNotIn("/", value)
        self.assertNotIn("..", value)
        self.assertLessEqual(len(value), 180)
        self.assertEqual(value, _path_component("../../outside/private"))
        self.assertEqual("contact-123", _path_component("contact-123"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_datalogger_rejects_symlink_path_segments(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            link = root / "linked"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(CollectionError) as caught:
                _safe_file_path(root, "linked/secret.txt")
            self.assertEqual("symlink_denied", caught.exception.code)

    def test_datalogger_invalid_entry_limit_is_mapped_to_collection_error(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "sample.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(CollectionError) as caught:
                collect_file(temp, {"metric": "count", "max_entries": "nope"})
            self.assertEqual("entry_limit_invalid", caught.exception.code)

    def test_datalogger_rejects_header_injection_before_network_io(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_SENSOR_ALLOWED_HOSTS": "localhost", "SIMPLEOFFICE_SENSOR_TEST": "secret"}, clear=False):
            with self.assertRaises(CollectionError) as caught:
                collect_http({
                    "url": "http://localhost/value",
                    "header_env": "SIMPLEOFFICE_SENSOR_TEST",
                    "header_name": "Authorization\r\nX-Evil",
                })
            self.assertEqual("header_name_invalid", caught.exception.code)

    def test_ics_preview_rejects_pathological_unfolded_line(self):
        prefix = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:test\r\nDTSTART:20260903T120000Z\r\nSUMMARY:"
        content = prefix + ("A" * (MAX_UNFOLDED_LINE_CHARS + 1)) + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        with self.assertRaisesRegex(ValueError, "too long"):
            preview_ics(content)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_installation_identity_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.write_text("00000000-0000-4000-8000-000000000001\n", encoding="ascii")
            link = root / "installation-id"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                _read_installation_id(link)

    def test_service_roles_are_allowlisted_and_invalid_pids_do_not_match(self):
        self.assertEqual("web", _role("web"))
        with self.assertRaises(ValueError):
            _role("../../web")
        self.assertFalse(process_matches({"pid": -1, "marker": "launcher.py"}))


if __name__ == "__main__":
    unittest.main()
