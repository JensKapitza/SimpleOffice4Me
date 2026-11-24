"""LAN proxy global bind: explicit 0.0.0.0/:: opt-in plus start overrides."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from app.lan_proxy_config import (
    container_proxy_mode,
    global_proxy_mode,
    runtime_lan_proxy_settings,
    validate_lan_proxy_settings,
)


def _settings(**overrides):
    values = {
        "enabled": True, "bind_host": "127.0.0.1", "port": 8081,
        "tls_cert": "", "tls_key": "",
    }
    values.update(overrides)
    return values


class GlobalBindTests(unittest.TestCase):
    def test_wildcard_is_rejected_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIMPLEOFFICE_LAN_PROXY_GLOBAL", None)
            os.environ.pop("SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE", None)
            self.assertFalse(global_proxy_mode())
            with self.assertRaisesRegex(ValueError, "--global"):
                validate_lan_proxy_settings(_settings(bind_host="0.0.0.0"))
            with self.assertRaisesRegex(ValueError, "SIMPLEOFFICE_LAN_PROXY_GLOBAL"):
                validate_lan_proxy_settings(_settings(bind_host="0.0.0.0"))

    def test_global_opt_in_accepts_ipv4_and_ipv6_wildcard(self):
        with mock.patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_GLOBAL": "1"}):
            self.assertTrue(global_proxy_mode())
            self.assertEqual(
                "0.0.0.0",
                validate_lan_proxy_settings(_settings(bind_host="0.0.0.0"))["bind_host"])
            self.assertEqual(
                "::",
                validate_lan_proxy_settings(_settings(bind_host="::"))["bind_host"])

    def test_global_toggle_values(self):
        for value in ("1", "true", "TRUE", "yes", "on", " 1 "):
            with self.subTest(value=value), mock.patch.dict(
                    os.environ, {"SIMPLEOFFICE_LAN_PROXY_GLOBAL": value}):
                self.assertTrue(global_proxy_mode())
        for value in ("", "0", "no", "off", "2"):
            with self.subTest(value=value), mock.patch.dict(
                    os.environ, {"SIMPLEOFFICE_LAN_PROXY_GLOBAL": value}):
                self.assertFalse(global_proxy_mode())

    def test_container_mode_still_accepts_ipv4_wildcard(self):
        with mock.patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE": "yes"}):
            os.environ.pop("SIMPLEOFFICE_LAN_PROXY_GLOBAL", None)
            self.assertTrue(container_proxy_mode())
            self.assertEqual(
                "0.0.0.0",
                validate_lan_proxy_settings(_settings(bind_host="0.0.0.0"))["bind_host"])

    def test_public_and_multicast_hosts_stay_rejected(self):
        with mock.patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_GLOBAL": "1"}):
            with self.assertRaises(ValueError):
                validate_lan_proxy_settings(_settings(bind_host="8.8.8.8"))
            with self.assertRaises(ValueError):
                validate_lan_proxy_settings(_settings(bind_host="224.0.0.1"))
            with self.assertRaises(ValueError):
                validate_lan_proxy_settings(_settings(bind_host="not-an-ip"))
            self.assertEqual(
                "192.168.1.10",
                validate_lan_proxy_settings(_settings(bind_host="192.168.1.10"))["bind_host"])

    def test_runtime_bind_host_override_wins_over_persisted(self):
        with mock.patch.dict(os.environ, {
            "SIMPLEOFFICE_LAN_PROXY_GLOBAL": "1",
            "SIMPLEOFFICE_LAN_PROXY_BIND_HOST": "0.0.0.0",
        }):
            result = runtime_lan_proxy_settings(_settings(bind_host="127.0.0.1"))
            self.assertEqual("0.0.0.0", result["bind_host"])

    def test_runtime_bind_host_override_is_validated_strictly(self):
        with mock.patch.dict(os.environ, {
            "SIMPLEOFFICE_LAN_PROXY_GLOBAL": "1",
            "SIMPLEOFFICE_LAN_PROXY_BIND_HOST": "999.999.999.999",
        }):
            with self.assertRaises(ValueError):
                runtime_lan_proxy_settings(_settings())

    def test_runtime_port_override_is_validated_strictly(self):
        with mock.patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_PORT": "8443"}):
            result = runtime_lan_proxy_settings(_settings(port=8081))
            self.assertEqual(8443, result["port"])
        for bad in ("abc", "80", "70000", "0"):
            with self.subTest(port=bad), mock.patch.dict(
                    os.environ, {"SIMPLEOFFICE_LAN_PROXY_PORT": bad}):
                with self.assertRaises(ValueError):
                    runtime_lan_proxy_settings(_settings())

    def test_admin_save_path_ignores_runtime_overrides(self):
        with mock.patch.dict(os.environ, {
            "SIMPLEOFFICE_LAN_PROXY_BIND_HOST": "0.0.0.0",
            "SIMPLEOFFICE_LAN_PROXY_PORT": "8443",
        }):
            os.environ.pop("SIMPLEOFFICE_LAN_PROXY_GLOBAL", None)
            result = validate_lan_proxy_settings(_settings())
            self.assertEqual("127.0.0.1", result["bind_host"])
            self.assertEqual(8081, result["port"])


if __name__ == "__main__":
    unittest.main()
