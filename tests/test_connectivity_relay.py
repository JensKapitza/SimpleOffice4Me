from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from simpleoffice_connection_relay import (
    DEFAULT_RELAY_SETTINGS,
    TurnRelayService,
    ensure_turn_secret,
    https_proxy_profile,
    ice_servers,
    load_relay_settings,
    normalize_tunnel_target,
    render_turnserver_config,
    save_relay_secrets,
    save_relay_settings,
    ssh_proxy_command,
    turn_rest_credentials,
    validate_relay_settings,
)
from simpleoffice_mini_control import ControlStore, NETWORK_SERVICES
from simpleoffice_service_lifecycle import service_health


class ConnectivityRelayConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "mini-services.json"

    def enabled(self, **changes):
        value = dict(DEFAULT_RELAY_SETTINGS)
        value.update({
            "enabled": True,
            "public_host": "relay.example.test",
            "listen_ip": "127.0.0.1",
            "relay_ip": "127.0.0.1",
        })
        value.update(changes)
        return value

    def test_defaults_are_disabled_and_do_not_create_open_proxy(self):
        settings = load_relay_settings(self.config)
        self.assertFalse(settings["enabled"])
        self.assertFalse(settings["https_proxy_enabled"])
        self.assertEqual([], settings["tunnel_targets"])
        self.assertEqual("127.0.0.1", settings["listen_ip"])

    def test_activation_flags_require_real_booleans(self):
        for key in ("enabled", "tls_enabled", "https_proxy_enabled"):
            for value in ("true", "false", 0, 1, None, []):
                with self.subTest(key=key, value=value):
                    candidate = dict(DEFAULT_RELAY_SETTINGS)
                    candidate[key] = value
                    with self.assertRaisesRegex(ValueError, "true oder false"):
                        validate_relay_settings(candidate)

    def test_https_connect_requires_https_credentials_metadata_and_exact_targets(self):
        candidate = self.enabled(
            https_proxy_enabled=True,
            https_proxy_url="https://proxy.example.test:443",
            https_proxy_username="admin",
            tunnel_targets=["server.example.test:22", "10.20.0.5:22"],
        )
        clean = validate_relay_settings(candidate)
        self.assertEqual("https://proxy.example.test:443", clean["https_proxy_url"])
        self.assertEqual(["server.example.test:22", "10.20.0.5:22"], clean["tunnel_targets"])

        for url in ("http://proxy.example.test", "https://user:pass@proxy.example.test", "https://proxy.example.test/path"):
            with self.subTest(url=url):
                bad = dict(candidate, https_proxy_url=url)
                with self.assertRaises(ValueError):
                    validate_relay_settings(bad)

        with self.assertRaises(ValueError):
            validate_relay_settings(dict(candidate, tunnel_targets=[]))
        with self.assertRaises(ValueError):
            normalize_tunnel_target("*.example.test:22")

    def test_secrets_are_mode_0600_and_not_returned_by_proxy_profile(self):
        save_relay_settings(
            self.enabled(
                https_proxy_enabled=True,
                https_proxy_url="https://proxy.example.test",
                https_proxy_username="jens",
                tunnel_targets=["server.example.test:22"],
            ),
            self.config,
        )
        save_relay_secrets(self.config, turn_secret="T" * 32, proxy_password="proxy-secret")
        secret_path = self.config.parent / "mini-services" / "connectivity-relay-secrets.json"
        mode = stat.S_IMODE(secret_path.stat().st_mode)
        self.assertEqual(0o600, mode)
        profile = https_proxy_profile(self.config)
        serialized = json.dumps(profile)
        self.assertTrue(profile["password_configured"])
        self.assertNotIn("proxy-secret", serialized)
        self.assertNotIn("T" * 32, serialized)

    def test_turn_rest_credentials_are_short_lived_and_deterministic(self):
        save_relay_settings(self.enabled(credential_ttl=900), self.config)
        secret = "S" * 32
        save_relay_secrets(self.config, turn_secret=secret)
        credentials = turn_rest_credentials(self.config, "amy", now=1_700_000_000)
        self.assertEqual("1700000900:amy", credentials["username"])
        expected = base64.b64encode(
            hmac.new(secret.encode(), credentials["username"].encode(), hashlib.sha1).digest()
        ).decode("ascii")
        self.assertEqual(expected, credentials["credential"])
        servers = ice_servers(self.config, "amy", now=1_700_000_000)
        self.assertTrue(servers[0]["urls"][0].startswith("stun:"))
        self.assertTrue(servers[1]["urls"][0].startswith("turn:"))
        self.assertEqual(expected, servers[1]["credential"])

    def test_turnserver_config_enforces_auth_and_relay_limits(self):
        settings = self.enabled(external_ip="203.0.113.10", min_port=50000, max_port=50020)
        config = render_turnserver_config(settings, "Q" * 32)
        self.assertIn("use-auth-secret", config)
        self.assertIn("static-auth-secret=" + "Q" * 32, config)
        self.assertIn("no-cli", config)
        self.assertIn("no-multicast-peers", config)
        self.assertIn("no-loopback-peers", config)
        self.assertIn("min-port=50000", config)
        self.assertIn("max-port=50020", config)
        self.assertIn("external-ip=203.0.113.10/127.0.0.1", config)

    def test_ssh_proxy_command_rejects_non_allowlisted_target(self):
        save_relay_settings(
            self.enabled(
                https_proxy_enabled=True,
                https_proxy_url="https://proxy.example.test",
                https_proxy_username="jens",
                tunnel_targets=["server.example.test:22"],
            ),
            self.config,
        )
        save_relay_secrets(self.config, proxy_password="secret")
        command = ssh_proxy_command(self.config, "server.example.test:22")
        self.assertIn("-m simpleoffice_https_connect_tunnel", command)
        self.assertIn("server.example.test:22", command)
        with self.assertRaisesRegex(ValueError, "nicht.*freigegeben"):
            ssh_proxy_command(self.config, "other.example.test:22")


class ConnectivityRelayLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "mini-services.json"
        self.settings = dict(DEFAULT_RELAY_SETTINGS)
        self.settings.update({
            "enabled": True,
            "public_host": "relay.example.test",
            "listen_ip": "127.0.0.1",
            "relay_ip": "127.0.0.1",
        })

    def test_control_store_exposes_relay_without_changing_other_services(self):
        self.assertIn("relay", NETWORK_SERVICES)
        preferences = ControlStore(self.config).preferences()
        self.assertIn("relay", preferences)
        self.assertTrue(preferences["relay"]["enabled"])

    def test_turn_service_lifecycle_owns_one_subprocess(self):
        process = MagicMock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch("simpleoffice_connection_relay.shutil.which", return_value="/usr/bin/turnserver"), \
             patch("simpleoffice_connection_relay.subprocess.Popen", return_value=process) as popen:
            service = TurnRelayService(self.settings, self.config)
            service.start()
            self.assertTrue(service_health(service))
            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertEqual("/usr/bin/turnserver", command[0])
            self.assertEqual("-c", command[1])
            turn_config = Path(command[2]).read_text(encoding="utf-8")
            self.assertIn("use-auth-secret", turn_config)
            self.assertNotIn(ensure_turn_secret(self.config), json.dumps(service.status()))
            service.stop()
            process.terminate.assert_called_once()
            self.assertFalse(service_health(service))

    def test_missing_turnserver_fails_without_claiming_running_state(self):
        with patch("simpleoffice_connection_relay.shutil.which", return_value=None):
            service = TurnRelayService(self.settings, self.config)
            with self.assertRaises(FileNotFoundError):
                service.start()
            self.assertFalse(service_health(service))


if __name__ == "__main__":
    unittest.main()
