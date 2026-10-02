import os
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
        row = sftp_service.status()
        self.assertEqual("integrated", row["settings"]["mode"])
        self.assertEqual("stopped", row["state"])
        self.assertFalse(row["system_service"]["available"])
        self.assertIn("start", row["capabilities"])

    def test_settings_are_persistent_strict_and_do_not_start_services(self):
        with patch("app.sftp_service._start") as start:
            saved = sftp_service.save_settings({"enabled": True, "autostart": True, "port": 2233})
            self.assertEqual(saved, sftp_service.settings())
            start.assert_not_called()
        for candidate in ({"enabled": "true"}, {"port": 0}, {"port": True}, {"bind": "host;cmd"}, {"mode": "auto"}, None):
            with self.subTest(candidate=candidate), self.assertRaises((ValueError, TypeError)):
                sftp_service.save_settings(candidate)

    def test_system_mode_is_explicit_and_preserves_os_startup_policy(self):
        with self.assertRaises(ValueError):
            sftp_service.save_settings({"mode": "system"})
        with patch("app.sftp_service.system_service", return_value={"available": True, "running": True, "unit": "ssh.service"}):
            sftp_service.save_settings({"mode": "system", "enabled": True, "autostart": True})
            self.assertEqual("system", sftp_service.status()["owner"])
            with patch("app.sftp_service.action") as action:
                self.assertIsNone(sftp_service.autostart(self.root))
                action.assert_not_called()

    def test_missing_optional_dependency_remains_visible(self):
        with patch("tools.sftp_setup.dependency", side_effect=RuntimeError("missing")):
            self.assertEqual("unavailable", sftp_service.safe_status()["state"])

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
