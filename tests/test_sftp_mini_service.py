import os
import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import sftp_service
from tools import sftp_setup


class SftpMiniServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target, value in (
            ("app.sftp_service.settings_path", self.root / "sftp.json"),
            ("app.sftp_service.system_service", {"available": False, "running": False, "unit": ""}),
            ("tools.service_control.read", None),
        ):
            context = patch(target, return_value=value)
            context.start(); self.addCleanup(context.stop)

    def test_defaults_are_integrated_stopped_and_do_not_require_openssh(self):
        with patch("tools.sftp_setup.dependency", return_value=object()), \
                patch.dict(os.environ, {"SIMPLEOFFICE_SFTP_MAX_BYTES": str(sftp_service.DEFAULT_MAX_UPLOAD_BYTES)}):
            row = sftp_service.status()
        self.assertEqual("integrated", row["settings"]["mode"])
        self.assertFalse(row["settings"]["rsync_enabled"])
        self.assertEqual(512 * 1024 * 1024, row["settings"]["max_bytes"])
        self.assertEqual("stopped", row["state"])
        self.assertFalse(row["system_service"]["available"])
        self.assertIn("start", row["capabilities"])

    def test_settings_are_persistent_strict_and_do_not_start_services(self):
        with patch("app.sftp_service._start") as start:
            saved = sftp_service.save_settings({"enabled": True, "autostart": True, "port": 2233,
                                                "max_bytes": 1024 * 1024})
            self.assertEqual(saved, sftp_service.settings())
            start.assert_not_called()
        invalid_candidates = (
            {"enabled": "true"}, {"rsync_enabled": "true"}, {"port": 0},
            {"port": True}, {"max_bytes": True}, {"max_bytes": 1.5},
            {"max_bytes": 0}, {"max_bytes": 8 * 1024**3 + 1},
            {"bind": "host;cmd"}, {"mode": "auto"}, None,
        )
        for candidate in invalid_candidates:
            with self.subTest(candidate=candidate), self.assertRaises((ValueError, TypeError)):
                sftp_service.save_settings(candidate)

    def test_legacy_environment_settings_are_migrated_until_explicitly_saved(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_ENABLED": "true",
                                     "SIMPLEOFFICE_SFTP_MAX_BYTES": "4096"}):
            self.assertTrue(sftp_service.settings()["rsync_enabled"])
            self.assertEqual(4096, sftp_service.settings()["max_bytes"])
            legacy_path = self.root / "sftp.json"
            legacy_path.write_text('{"mode":"integrated"}', encoding="utf-8")
            self.assertTrue(sftp_service.settings()["rsync_enabled"])
            self.assertEqual(4096, sftp_service.settings()["max_bytes"])
            saved = sftp_service.save_settings({"rsync_enabled": False, "max_bytes": 8192})
            self.assertFalse(saved["rsync_enabled"])
            self.assertEqual(8192, saved["max_bytes"])
            self.assertFalse(sftp_service.settings()["rsync_enabled"])
            self.assertEqual(8192, sftp_service.settings()["max_bytes"])

    def test_integrated_child_gets_explicit_rsync_setting(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), \
                    patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_ENABLED": "true",
                                            "SIMPLEOFFICE_SFTP_MAX_BYTES": "3"}), \
                    patch("app.sftp_service.service_control.RUN_DIR", self.root), \
                    patch("app.sftp_service.service_control.read", side_effect=[None, {"pid": 42}]), \
                    patch("app.sftp_service.service_control.process_matches", return_value=False), \
                    patch("app.sftp_service.sftp_setup.initialize"), \
                    patch("app.sftp_service.sftp_setup.key_path", return_value=self.root / "host-key"), \
                    patch("app.sftp_service.subprocess.Popen") as popen, \
                    patch("app.sftp_service._ready", return_value=True):
                popen.return_value.pid = 42
                popen.return_value.poll.return_value = None
                sftp_service._start(
                    {"bind": "127.0.0.1", "port": 2222, "rsync_enabled": enabled,
                     "max_bytes": 1048576}, self.root,
                )
            self.assertEqual(
                "true" if enabled else "false",
                popen.call_args.kwargs["env"]["SIMPLEOFFICE_RSYNC_ENABLED"],
            )
            self.assertEqual("1048576", popen.call_args.kwargs["env"]["SIMPLEOFFICE_SFTP_MAX_BYTES"])

    def test_system_mode_is_explicit_and_preserves_os_startup_policy(self):
        with self.assertRaises(ValueError):
            sftp_service.save_settings({"mode": "system"})
        with patch("app.sftp_service.system_service", return_value={"available": True, "running": True, "unit": "ssh.service"}):
            sftp_service.save_settings({"mode": "system", "enabled": True, "autostart": True})
            self.assertEqual("system", sftp_service.status()["owner"])
            with patch("app.sftp_service.action") as action:
                self.assertIsNone(sftp_service.autostart(self.root))
                action.assert_not_called()

    def test_invalid_json_values_are_unavailable_without_server_errors(self):
        for candidate in ({"mode": []}, {"mode": {}}, {"port": None},
                          {"port": 2222.5}, {"system_port": []}, {"bind": None}):
            with self.subTest(candidate=candidate):
                (self.root / "sftp.json").write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(ValueError):
                    sftp_service.settings()
                self.assertEqual("unavailable", sftp_service.safe_status()["state"])

    def test_complete_settings_repair_corruption_but_partial_updates_preserve_it(self):
        path = self.root / "sftp.json"
        for raw in ("{broken", '{"mode": [], "port": null}', '["invalid"]'):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    sftp_service.save_settings({"enabled": True})
                self.assertEqual(raw, path.read_text(encoding="utf-8"))
                candidate = {**sftp_service.DEFAULTS, "port": 2233}
                self.assertEqual(candidate, sftp_service.save_settings(candidate))
                self.assertEqual(candidate, sftp_service.settings())

    def test_invalid_full_replacement_and_running_service_do_not_overwrite_corruption(self):
        path = self.root / "sftp.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            sftp_service.save_settings({**sftp_service.DEFAULTS, "port": 2222.5})
        with patch("tools.service_control.read", return_value={"pid": 1}), \
                patch("tools.service_control.process_matches", return_value=True):
            with self.assertRaises(RuntimeError):
                sftp_service.save_settings(dict(sftp_service.DEFAULTS))
        self.assertEqual("{broken", path.read_text(encoding="utf-8"))

    def test_corrupt_config_stops_only_registered_process_and_never_starts(self):
        (self.root / "sftp.json").write_text('{"mode": []}', encoding="utf-8")
        with patch("app.sftp_service.system_service") as system, \
                patch("tools.service_control.stop", return_value=True) as stop, \
                patch("app.sftp_service._start") as start:
            self.assertEqual("unavailable", sftp_service.action("stop", self.root)["state"])
            stop.assert_called_once_with(timeout=3, roles=["sftp"])
            for action in ("start", "restart"):
                with self.assertRaises(ValueError):
                    sftp_service.action(action, self.root)
            system.assert_not_called()
            start.assert_not_called()

    def test_missing_optional_dependency_remains_visible(self):
        with patch("tools.sftp_setup.dependency", side_effect=RuntimeError("missing")):
            sftp_service.save_settings({"port": 2233})
            for row in (sftp_service.status(), sftp_service.safe_status()):
                self.assertEqual("unavailable", row["state"])
                self.assertEqual(2233, row["settings"]["port"])
                self.assertIn("Paramiko fehlt", row["health"]["message"])

    def test_running_service_blocks_config_changes(self):
        with patch("tools.service_control.read", return_value={"pid": 1}), patch("tools.service_control.process_matches", return_value=True):
            with self.assertRaises(RuntimeError):
                sftp_service.save_settings({"port": 2233})

    def test_autostart_requires_both_enabled_and_preference(self):
        with patch("app.sftp_service.action") as action:
            sftp_service.autostart(self.root)
            action.assert_not_called()
            sftp_service.save_settings({"enabled": True, "autostart": True})
            sftp_service.autostart(self.root)
            action.assert_called_once_with("start", self.root)

    @unittest.skipUnless(importlib.util.find_spec("paramiko"), "optional SFTP extra required for real host keys")
    def test_key_init_is_idempotent_private_and_rejects_symlinks(self):
        key = self.root / "host-key"
        sftp_setup.initialize(key)
        content = key.read_bytes()
        sftp_setup.initialize(key)
        self.assertEqual(content, key.read_bytes())
        if os.name != "nt":
            self.assertEqual(0o600, key.stat().st_mode & 0o777)
            link = self.root / "linked-key"
            link.symlink_to(key)
            with self.assertRaises(RuntimeError):
                sftp_setup.initialize(link)

    def test_actions_are_serialized_and_stopped_service_cannot_start_disabled(self):
        with patch("tools.service_control.RUN_DIR", self.root):
            with self.assertRaises(ValueError):
                sftp_service.action("start", self.root)
            with patch("tools.service_control.stop", return_value=True) as stop:
                sftp_service.action("stop", self.root)
                stop.assert_called_once_with(timeout=3, roles=["sftp"])

    def test_scan_is_local_and_does_not_launch_service(self):
        with patch("app.sftp_service._start") as start:
            result = sftp_service.action("scan", self.root)
            self.assertEqual(1, result["count"])
            self.assertEqual("completed", result["state"])
            start.assert_not_called()

    def test_start_lock_also_blocks_concurrent_settings_updates(self):
        with patch("tools.service_control.RUN_DIR", self.root):
            with sftp_service.service_control.exclusive_lease(self.root / "sftp-control.lock"):
                with self.assertRaises(RuntimeError):
                    sftp_service.save_settings({"port": 2233})

    def test_readiness_uses_loopback_for_unspecified_ipv4_and_ipv6(self):
        for host, expected in (("0.0.0.0", "127.0.0.1"), ("::", "::1"), ("127.0.0.2", "127.0.0.2")):
            with self.subTest(host=host), patch("app.sftp_service.socket.create_connection") as connect:
                connect.return_value.__enter__.return_value.recv.return_value = b"SSH-2.0-paramiko_test"
                self.assertTrue(sftp_service._ready({"bind": host, "port": 2222}))
                connect.assert_called_once_with((expected, 2222), timeout=.3)
