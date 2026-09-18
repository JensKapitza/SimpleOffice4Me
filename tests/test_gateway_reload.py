"""Atomic Linux replacement is reached without a destructive worker stop."""
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tools.mini_services import Worker
from simpleoffice_network_gateway_runtime import load_gateway_ownership
from tests.test_gateway_ownership_recovery import _GATEWAY


class GatewayReloadTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.worker = Worker(Path(directory) / "mini.json")
        self.settings = self.stack.enter_context(patch("tools.mini_services.load_gateway_settings", return_value=dict(_GATEWAY)))
        self.stack.enter_context(patch("tools.mini_services.platform_kind", return_value="linux"))
        self.stack.enter_context(patch.object(self.worker, "_start_one"))
        self.stack.enter_context(patch.object(self.worker, "_network_available", return_value=True))
        self.worker.manual_states["gateway"] = True
        self.worker._load_network_services()
        self.worker.gateway_active = True
        self.worker.gateway_status = {"ok": True, "platform": "linux", "mode": "nat"}
        self.worker.states["gateway"].running()
        self.previous = self.worker.desired["gateway"]
        self.started_at = self.worker.states["gateway"].started_at
        self.apply = self.stack.enter_context(patch("tools.mini_services.apply_gateway", return_value={"ok": True, "platform": "linux", "mode": "route"}))
        self.stop = self.stack.enter_context(patch("tools.mini_services.disable_gateway", return_value={"ok": True}))

    def change(self):
        self.settings.return_value = {**_GATEWAY, "mode": "route"}

    def test_config_reload_preserves_uptime_and_commits_only_after_apply(self):
        self.change()
        def apply(*args, **kwargs):
            self.assertEqual(self.previous, self.worker.desired["gateway"])
            self.assertEqual("route", load_gateway_ownership(self.worker.config_path)["mode"])
            return {"ok": True, "platform": "linux", "mode": "route"}
        self.apply.side_effect = apply
        self.worker._load_network_services()
        self.assertEqual("route", self.worker.desired["gateway"][1]["mode"])
        self.assertEqual(self.started_at, self.worker.states["gateway"].started_at)
        self.stop.assert_not_called()
        self.apply.assert_called_once()
        self.worker._load_network_services()
        self.apply.assert_called_once()

    def test_rejection_and_timeout_keep_owned_rules_and_active_settings(self):
        self.change()
        for exc in (RuntimeError("private detail"), subprocess.TimeoutExpired("nft", 5)):
            self.apply.side_effect = exc
            with self.assertRaises(type(exc)):
                self.worker._load_network_services()
            self.assertEqual(self.previous, self.worker.desired["gateway"])
            self.assertTrue(self.worker.gateway_active)
            self.assertEqual("degraded", self.worker.states["gateway"].state)
            self.assertNotIn("private detail", str(self.worker.states["gateway"].last_error))
            self.assertIsNotNone(load_gateway_ownership(self.worker.config_path))
        self.stop.assert_not_called()

    def test_marker_failure_prevents_apply_without_stopping_active_rules(self):
        self.change()
        with patch("tools.mini_services.remember_gateway_ownership", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.worker._load_network_services()
        self.apply.assert_not_called()
        self.stop.assert_not_called()
        self.assertEqual(self.previous, self.worker.desired["gateway"])

    def test_explicit_identical_restart_uses_reload_and_reports_failure(self):
        self.apply.return_value = dict(self.worker.gateway_status)
        command = self.worker.control.enqueue("gateway", "restart")
        self.worker._execute(self.worker.control.claim())
        self.apply.assert_called_once()
        self.stop.assert_not_called()
        self.assertEqual("completed", self.worker.control.operation(command["id"])["state"])
        self.apply.side_effect = RuntimeError("rejected")
        command = self.worker.control.enqueue("gateway", "restart")
        self.worker._execute(self.worker.control.claim())
        self.assertEqual("failed", self.worker.control.operation(command["id"])["state"])
        self.assertTrue(self.worker.gateway_active)
        self.stop.assert_not_called()

    def test_disabling_still_removes_owned_rules(self):
        self.settings.return_value = {**_GATEWAY, "enabled": False}
        self.worker._load_network_services()
        self.stop.assert_called_once()
        self.apply.assert_not_called()
        self.assertFalse(self.worker.gateway_active)

    def test_windows_changed_network_replaces_owned_resource(self):
        self.settings.return_value = {**_GATEWAY, "internal_network": "192.168.99.0/24"}
        command = self.worker.control.enqueue("gateway", "restart")
        with patch("tools.mini_services.platform_kind", return_value="windows"):
            self.worker._execute(self.worker.control.claim())
        self.stop.assert_called_once()
        self.apply.assert_called_once()
        self.assertTrue(self.worker.gateway_active)
        self.assertEqual("192.168.99.0/24", self.worker.desired["gateway"][1]["internal_network"])
        self.assertEqual("completed", self.worker.control.operation(command["id"])["state"])

    def test_windows_same_nat_restart_preserves_active_resource_on_failure(self):
        command = self.worker.control.enqueue("gateway", "restart")
        self.apply.side_effect = PermissionError("private diagnostic")
        with patch("tools.mini_services.platform_kind", return_value="windows"):
            self.worker._execute(self.worker.control.claim())
        self.stop.assert_not_called()
        self.apply.assert_called_once()
        self.assertTrue(self.worker.gateway_active)
        self.assertEqual("failed", self.worker.control.operation(command["id"])["state"])

    def test_windows_failed_change_restores_previous_configuration(self):
        self.settings.return_value = {**_GATEWAY, "internal_network": "192.168.99.0/24"}
        for error in (PermissionError("secret"), subprocess.TimeoutExpired("powershell", 30)):
            with self.subTest(error=type(error).__name__):
                self.previous[1]["_server_ip"] = "192.168.178.77"
                self.apply.reset_mock()
                self.apply.side_effect = [error, {"ok": True, "platform": "windows", "mode": "nat"}]
                command = self.worker.control.enqueue("gateway", "restart")
                with patch("tools.mini_services.platform_kind", return_value="windows"):
                    self.worker._execute(self.worker.control.claim())
                self.assertEqual("failed", self.worker.control.operation(command["id"])["state"])
                self.assertTrue(self.worker.gateway_active)
                self.assertEqual(self.previous, self.worker.desired["gateway"])
                self.assertEqual(self.previous[1]["internal_network"], load_gateway_ownership(self.worker.config_path)["internal_network"])
                self.assertEqual("degraded", self.worker.states["gateway"].state)
                self.assertIsNone(self.worker.states["gateway"].retry_at)
                self.assertNotIn("secret", str(self.worker.states["gateway"].last_error))
                self.assertEqual(2, self.apply.call_count)
                self.assertEqual("192.168.178.77", self.apply.call_args.kwargs["server_ip"])

    def test_windows_failed_cleanup_does_not_overwrite_ownership_or_restore(self):
        self.settings.return_value = {**_GATEWAY, "internal_network": "192.168.99.0/24"}
        self.apply.side_effect = RuntimeError("apply rejected")
        self.stop.side_effect = [{"ok": True}, PermissionError("cleanup denied")]
        with patch("tools.mini_services.platform_kind", return_value="windows"), self.assertRaises(RuntimeError):
            self.worker._load_network_services()
        self.apply.assert_called_once()
        self.assertFalse(self.worker.gateway_active)
        self.assertEqual("192.168.99.0/24", load_gateway_ownership(self.worker.config_path)["internal_network"])
        self.assertEqual("failed", self.worker.states["gateway"].state)
        self.assertIsNone(self.worker.states["gateway"].retry_at)

    def test_windows_restore_failure_retains_previous_marker_for_manual_cleanup(self):
        self.settings.return_value = {**_GATEWAY, "internal_network": "192.168.99.0/24"}
        self.apply.side_effect = [RuntimeError("new rejected"), PermissionError("old rejected")]
        with patch("tools.mini_services.platform_kind", return_value="windows"), self.assertRaises(RuntimeError):
            self.worker._load_network_services()
        self.assertFalse(self.worker.gateway_active)
        self.assertEqual(self.previous[1]["internal_network"], load_gateway_ownership(self.worker.config_path)["internal_network"])
        self.assertEqual("failed", self.worker.states["gateway"].state)
        self.assertIsNone(self.worker.states["gateway"].retry_at)

    def test_windows_name_change_never_touches_unowned_nat(self):
        self.settings.return_value = {**_GATEWAY, "nat_name": "OtherNat"}
        with patch("tools.mini_services.platform_kind", return_value="windows"), self.assertRaises(ValueError):
            self.worker._load_network_services()
        self.stop.assert_not_called()
        self.apply.assert_not_called()
        self.assertTrue(self.worker.gateway_active)

    def test_restored_configuration_is_not_replaced_again_on_next_tick(self):
        self.settings.return_value = {**_GATEWAY, "internal_network": "192.168.99.0/24"}
        self.apply.side_effect = [RuntimeError("new rejected"), {"ok": True, "platform": "windows", "mode": "nat"}]
        self.worker.config_loaded = True
        self.worker.next_gateway_health = float("inf")
        self.worker.next_network_scan = float("inf")
        with patch("tools.mini_services.platform_kind", return_value="windows"):
            with self.assertRaises(RuntimeError):
                self.worker._load_network_services()
            self.worker.next_gateway_health = float("inf")
            self.worker.tick()
        self.assertEqual(2, self.apply.call_count)
        self.assertEqual(self.previous, self.worker.desired["gateway"])

    def test_windows_initial_stop_failure_prevents_new_apply(self):
        self.settings.return_value = {**_GATEWAY, "internal_network": "192.168.99.0/24"}
        self.stop.side_effect = PermissionError("stop denied")
        with patch("tools.mini_services.platform_kind", return_value="windows"), self.assertRaises(RuntimeError):
            self.worker._load_network_services()
        self.apply.assert_not_called()
        self.assertEqual(self.previous, self.worker.desired["gateway"])
        self.assertIsNone(self.worker.states["gateway"].retry_at)
