"""Worker recovery tests without touching host networking or using root."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tools.mini_services import Worker


class GatewayHealthRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.worker = Worker(Path(directory) / "mini.json")
        self.worker.config_loaded = True
        self.worker.config = {"dhcp": {"server_ip": "192.168.1.1"}}
        self.worker.desired["gateway"] = (True, {"enabled": True, "mode": "nat"})
        self.worker.gateway_active = True
        self.worker.states["gateway"].running()
        for name, value in (("_signature", self.worker.config_signature), ("_network_available", True),
                            ("_refresh_network", None), ("write_status", None)):
            self.stack.enter_context(patch.object(self.worker, name, return_value=value))
        self.clock = self.stack.enter_context(patch("tools.mini_services.time.monotonic", return_value=100.0))
        self.health = self.stack.enter_context(patch("tools.mini_services.gateway_health", return_value={"ok": False, "message": "Regeln fehlen"}))
        self.stop = self.stack.enter_context(patch("tools.mini_services.disable_gateway", return_value={"ok": True}))
        self.start = self.stack.enter_context(patch("tools.mini_services.apply_gateway", return_value={"ok": True, "platform": "linux", "mode": "nat"}))

    def test_confirmed_failure_retries_after_backoff_and_checks_replacement(self):
        self.worker.tick()
        state = self.worker.states["gateway"]
        self.assertEqual("failed", state.state)
        self.assertEqual(102, state.retry_at)
        self.start.assert_not_called()
        self.stop.assert_not_called()
        self.clock.return_value = 101
        self.worker.tick()
        self.start.assert_not_called()
        self.clock.return_value = 102
        self.worker.tick()
        self.start.assert_called_once()
        self.stop.assert_called_once()
        self.health.return_value = {"ok": True, "message": "Regeln vorhanden"}
        self.worker.tick()
        self.assertEqual("running", state.state)
        self.assertTrue(self.worker.gateway_health["ok"])

    def test_unreadable_health_never_restarts_and_can_become_readable(self):
        self.health.return_value = {"ok": None, "message": "Berechtigung fehlt"}
        self.worker.tick()
        state = self.worker.states["gateway"]
        self.assertEqual("degraded", state.state)
        self.assertIsNone(state.retry_at)
        self.clock.return_value = 115
        self.health.return_value = {"ok": True, "message": "OK"}
        self.worker.tick()
        self.assertEqual("running", state.state)
        self.stop.assert_not_called()
        self.start.assert_not_called()

    def test_repeated_health_failures_exhaust_existing_budget(self):
        state = self.worker.states["gateway"]
        for count in range(1, 7):
            self.worker.next_gateway_health = 0
            self.worker.tick()
            self.assertEqual(count, state.retry_count)
            self.assertEqual("failed", state.state)
            if count < 6:
                self.clock.return_value = state.retry_at
                self.worker.tick()
                self.assertEqual("running", state.state)
        self.assertIsNone(state.retry_at)
        self.clock.return_value += 1000
        self.worker.tick()
        self.assertEqual(5, self.start.call_count)

    def test_explicit_stop_cancels_scheduled_health_recovery(self):
        self.worker.tick()
        self.worker.control.enqueue("gateway", "stop")
        self.clock.return_value = 103
        self.worker.tick()
        self.clock.return_value = 200
        self.worker.tick()
        self.assertFalse(self.worker.desired["gateway"][0])
        self.assertFalse(self.worker.gateway_active)
        self.assertIsNone(self.worker.states["gateway"].retry_at)
        self.start.assert_not_called()
