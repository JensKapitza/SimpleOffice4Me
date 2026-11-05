"""Proxy status monitoring regression tests (no Electron required)."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.desktop_proxy_status import _desktop_proxy_status


class DesktopProxyStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / ".simpleoffice-meta" / "desktop-proxy-status.json"
        self.file.parent.mkdir(parents=True)

    def write(self, **values):
        self.file.write_text(json.dumps({
            "schema": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "state": "running",
            "origin": "https://192.168.1.20:8081",
            "message": "Proxy running",
            **values,
        }), encoding="utf-8")

    def test_running_status_displays_origin(self):
        self.write()
        result = _desktop_proxy_status(self.root)
        self.assertEqual("running", result["state"])
        self.assertEqual("https://192.168.1.20:8081", result["origin"])

    def test_stale_status_is_not_treated_as_running(self):
        self.write(updated_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        result = _desktop_proxy_status(self.root)
        self.assertEqual("stale", result["state"])
        self.assertEqual("", result["origin"])

    def test_error_does_not_offer_nonexistent_url(self):
        self.write(state="error")
        result = _desktop_proxy_status(self.root)
        self.assertEqual("error", result["state"])
        self.assertEqual("", result["origin"])

    def test_invalid_or_missing_status_is_unknown(self):
        self.assertEqual("unknown", _desktop_proxy_status(self.root)["state"])
        self.file.write_text('{"updated_at":"broken"}', encoding="utf-8")
        self.assertEqual("unknown", _desktop_proxy_status(self.root)["state"])

    def test_plaintext_http_lan_origin_is_never_advertised(self):
        self.write(origin="http://192.168.1.20:8081")
        self.assertEqual("", _desktop_proxy_status(self.root)["origin"])

    def test_untrusted_scheme_is_not_linked(self):
        self.write(origin="javascript:alert(1)")
        self.assertEqual("", _desktop_proxy_status(self.root)["origin"])


if __name__ == "__main__":
    unittest.main()
