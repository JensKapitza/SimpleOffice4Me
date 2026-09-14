"""Boundary regressions requiring neither network access nor privileged ports."""
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from app.network_boot_http import bp, federation_bp
from simpleoffice_mini_core import _atomic_write, _BlocklistRedirectHandler, _https_open, validate_config
from simpleoffice_network_boot import assets_root, boot_settings_path, save_boot_settings, validate_boot_settings
from simpleoffice_network_gateway import validate_gateway_settings
from simpleoffice_sip_runtime import SipRegistrarService


class ConfigurationSecurityTests(unittest.TestCase):
    def test_boolean_strings_cannot_enable_network_services(self):
        for value in ("false", "true", 0, 1, None, []):
            for section in ("dhcp", "dns"):
                with self.subTest(section=section, value=value), self.assertRaises(ValueError):
                    validate_config({section: {"enabled": value}})
            for validator in (validate_boot_settings, validate_gateway_settings):
                with self.subTest(validator=validator, value=value), self.assertRaises(ValueError):
                    validator({"enabled": value})

    def test_boot_scripts_reject_url_line_injection_and_malformed_profiles(self):
        for url in ("https://server/boot\nreboot", "http://user:secret@server/", "http://"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_boot_settings({"http_base_url": url})
        for profiles in (None, {}, ["not a profile"]):
            with self.assertRaises(ValueError):
                validate_boot_settings({"profiles": profiles})
        profile = {"id": "test", "mode": "kernel", "kernel": "kernel", "label": "Linux\nreboot"}
        self.assertNotIn("\n", validate_boot_settings({"profiles": [profile]})["profiles"][0]["label"])

    def test_redirect_is_rejected_before_next_request_is_created(self):
        handler = _BlocklistRedirectHandler()
        request = urllib.request.Request("https://feed.example/blocklist")
        for url in ("http://feed.example/insecure", "https://user:secret@feed.example/list", "file:///tmp/list"):
            with self.subTest(url=url), patch.object(urllib.request.HTTPRedirectHandler, "redirect_request") as follow:
                with self.assertRaises(ValueError):
                    handler.redirect_request(request, None, 302, "Found", {}, url)
                follow.assert_not_called()
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://cdn.example/list")
        self.assertEqual("https://cdn.example/list", redirected.full_url)

    def test_blocklist_opener_installs_guard_and_checks_initial_address(self):
        with patch("simpleoffice_mini_core.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.geturl.return_value = "https://feed.example/list"
            _https_open(urllib.request.Request("https://feed.example/list"), 2)
            self.assertIsInstance(opener.call_args.args[0], _BlocklistRedirectHandler)
        with patch("simpleoffice_mini_core.urllib.request.build_opener") as opener:
            with self.assertRaises(ValueError):
                _https_open(urllib.request.Request("http://feed.example/list"), 2)
            opener.assert_not_called()

    def test_atomic_write_closes_descriptor_if_wrapping_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch("simpleoffice_mini_core.os.fdopen", side_effect=OSError("cannot wrap")) as wrap:
                with self.assertRaises(OSError):
                    _atomic_write(path, b"{}")
            with self.assertRaises(OSError):
                os.fstat(wrap.call_args.args[0])
            self.assertEqual([], list(Path(directory).iterdir()))


class NetworkBootExposureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "mini.json"
        env = patch.dict(os.environ, {"SIMPLEOFFICE_MINI_SERVICES_CONFIG": str(self.path)})
        env.start(); self.addCleanup(env.stop)
        root = assets_root(self.path)
        root.mkdir(parents=True)
        (root / "kernel").write_bytes(b"boot-content")
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(bp)
        self.app.register_blueprint(federation_bp)
        self.client = self.app.test_client()

    def test_disabled_boot_never_publishes_assets_or_script(self):
        for method in ("GET", "HEAD"):
            for url in ("/network-boot/files/kernel", "/network-boot/ipxe"):
                with self.client.open(url, method=method) as response:
                    self.assertEqual(404, response.status_code)
                    self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_enabled_boot_serves_ranges_and_stop_revokes_access(self):
        save_boot_settings({"enabled": True, "default_profile": "linux", "profiles": [
            {"id": "linux", "kernel": "kernel"}]}, self.path)
        with self.client.get("/network-boot/files/kernel", headers={"Range": "bytes=0-3"}) as response:
            self.assertEqual(206, response.status_code)
            self.assertEqual(b"boot", response.data)
        with self.client.get("/network-boot/ipxe") as response:
            self.assertEqual(200, response.status_code)
            self.assertIn(b"/network-boot/files/kernel", response.data)
        save_boot_settings({"enabled": False}, self.path)
        self.assertEqual(404, self.client.get("/network-boot/files/kernel").status_code)

    def test_corrupt_settings_fail_closed_and_federation_stays_authenticated(self):
        settings = boot_settings_path(self.path)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"enabled": "false"}')
        self.assertEqual(503, self.client.get("/network-boot/files/kernel").status_code)
        with patch("app.network_boot_http._authorized", return_value=False):
            self.assertEqual(401, self.client.get("/federation/v1/network-boot/assets/kernel").status_code)
        with patch("app.network_boot_http._authorized", return_value=True):
            with self.client.get("/federation/v1/network-boot/assets/kernel") as response:
                self.assertEqual(200, response.status_code)


class SipBoundsTests(unittest.TestCase):
    def test_challenges_evict_oldest_nonce_and_its_replay_count(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SipRegistrarService(Path(directory) / "mini.json")
            with patch("simpleoffice_sip_runtime._MAX_NONCES", 4):
                service._challenge({}, "127.0.0.1")
                oldest = next(iter(service.nonces))
                service.nonce_counts[(oldest, "101")] = 1
                for _ in range(10):
                    service._challenge({}, "127.0.0.1")
                self.assertEqual(4, len(service.nonces))
                self.assertNotIn(oldest, service.nonces)
                self.assertNotIn((oldest, "101"), service.nonce_counts)


if __name__ == "__main__":
    unittest.main()
