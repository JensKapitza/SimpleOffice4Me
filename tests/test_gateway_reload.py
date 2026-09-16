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

    def test_windows_restart_keeps_existing_stop_start_path(self):
        command = self.worker.control.enqueue("gateway", "restart")
        with patch("tools.mini_services.platform_kind", return_value="windows"):
            self.worker._execute(self.worker.control.claim())
        self.stop.assert_called_once()
        self.apply.assert_not_called()
        self.worker._start_one.assert_called_with("gateway")
