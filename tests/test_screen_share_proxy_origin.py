"""Screen sharing must not expose a configured but unavailable LAN proxy."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

from app.screen_share import _screen_proxy_origin


class ScreenShareProxyOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.status_file = self.root / ".simpleoffice-meta" / "desktop-proxy-status.json"
        self.status_file.parent.mkdir(parents=True)
        self.app = Flask(__name__)
        self.app.config["DOCUMENT_ROOT"] = self.root

    def status(self, **overrides):
        row = {
            "schema": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "state": "running",
            "origin": "https://192.168.1.50:8081",
            "message": "HTTPS listener ready",
        }
        row.update(overrides)
        self.status_file.write_text(json.dumps(row), encoding="utf-8")

    def test_running_https_proxy_is_advertised(self):
        self.status()
        with self.app.app_context():
            self.assertEqual("https://192.168.1.50:8081", _screen_proxy_origin())

    def test_missing_disabled_or_failed_proxy_is_never_advertised(self):
        with self.app.app_context():
            self.assertEqual("", _screen_proxy_origin())
            for state in ("error", "stopped", "disabled"):
                with self.subTest(state=state):
                    self.status(state=state)
                    self.assertEqual("", _screen_proxy_origin())

    def test_expired_heartbeat_does_not_offer_a_dead_url(self):
        self.status(updated_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        with self.app.app_context():
            self.assertEqual("", _screen_proxy_origin())

    def test_plain_http_is_never_advertised(self):
        self.status(origin="http://192.168.1.50:8081")
        with self.app.app_context():
            self.assertEqual("", _screen_proxy_origin())


if __name__ == "__main__":
    unittest.main()
