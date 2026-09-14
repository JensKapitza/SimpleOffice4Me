import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simpleoffice_network_gateway import interfaces_snapshot, binding_available
from simpleoffice_mini_services import save_config
from tools.mini_services import Worker


UP = {"available": True, "interfaces": [{"name": "lan", "state": "UP", "addresses": ["192.168.50.1/24"]}], "default_interfaces": ["lan"]}
DOWN = {"available": True, "interfaces": [], "default_interfaces": []}


class NetworkDiscoveryTests(unittest.TestCase):
    def test_windows_uses_existing_inventory_and_preserves_prefix(self):
        data = {"InterfaceAlias": "LAN", "InterfaceIndex": 2, "Status": "Up", "IPv4Address": {"IPAddress": "192.168.50.1", "PrefixLength": 24}, "IPv4DefaultGateway": {"NextHop": "192.168.50.254"}}
        with patch("simpleoffice_network_gateway.platform_kind", return_value="windows"), patch("simpleoffice_network_gateway._powershell", side_effect=[{"ok": True, "stdout": json.dumps(data)}, {"ok": True, "stdout": "1"}]) as run:
            status = interfaces_snapshot()
        self.assertTrue(status["available"])
        self.assertEqual(["192.168.50.1/24"], status["interfaces"][0]["addresses"])
        self.assertEqual(["LAN"], status["default_interfaces"])
        self.assertTrue(all(call.args[1] == 3 for call in run.call_args_list))

    def test_invalid_inventory_is_unknown_not_empty_hardware(self):
        for platform, target in (("windows", "_powershell"), ("linux", "_run")):
            with patch("simpleoffice_network_gateway.platform_kind", return_value=platform), patch("simpleoffice_network_gateway." + target, return_value={"ok": True, "stdout": "invalid json"}):
                self.assertFalse(interfaces_snapshot()["available"])

    def test_explicit_addresses_and_interfaces_are_checked_but_unknown_is_not_missing(self):
        settings = {"bind": ["192.168.50.1"], "interface": "lan"}
        self.assertTrue(binding_available(settings, UP, addresses=("bind",), interfaces=("interface",)))
        self.assertFalse(binding_available(settings, DOWN, addresses=("bind",)))
        self.assertTrue(binding_available(settings, {"available": False}, addresses=("bind",)))
        self.assertTrue(binding_available({"bind": ["127.0.0.1", "::1"]}, DOWN, addresses=("bind",)))
        self.assertFalse(binding_available(settings, {**UP, "interfaces": [{**UP["interfaces"][0], "state": "DOWN"}]}, interfaces=("interface",)))


class NetworkRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "mini.json"
        save_config({"dns": {"enabled": True, "bind": ["192.168.50.1"], "port": 15353}}, self.path)
        self.worker = Worker(self.path)
        self.worker.control.save_preferences("sip", {"enabled": False, "autostart": False})
        self.factory = patch("tools.mini_services.DnsService")
        self.dns = self.factory.start(); self.addCleanup(self.factory.stop)
        self.health = patch("tools.mini_services.service_health", side_effect=lambda service: service is not None)
        self.health.start(); self.addCleanup(self.health.stop)

    def scan(self, snapshot):
        self.worker.next_network_scan = 0
        with patch("tools.mini_services.interfaces_snapshot", return_value=snapshot):
            self.worker.tick()

    def test_removed_address_stops_owned_listener_and_return_restarts_once(self):
        self.scan(UP)
        self.assertEqual("running", self.worker.states["dns"].state)
        first_count = self.dns.call_count
        self.scan(DOWN)
        self.assertEqual("waiting", self.worker.states["dns"].state)
        self.assertIsNone(self.worker.dns)
        self.scan(DOWN)
        self.assertEqual(first_count, self.dns.call_count)
        self.scan(UP)
        self.assertEqual("running", self.worker.states["dns"].state)
        self.assertEqual(first_count + 1, self.dns.call_count)
        self.scan(UP)
        self.assertEqual(first_count + 1, self.dns.call_count)

    def test_explicit_stop_cancels_waiting_and_network_return_does_not_restart(self):
        self.scan(UP)
        self.scan(DOWN)
        self.worker.control.enqueue("dns", "stop")
        self.worker.tick()
        count = self.dns.call_count
        self.scan(UP)
        self.assertEqual(count, self.dns.call_count)
        self.assertNotEqual("running", self.worker.states["dns"].state)

    def test_unknown_scan_does_not_stop_a_working_service_or_spin(self):
        self.scan(UP)
        count = self.dns.call_count
        self.scan({"available": False, "interfaces": []})
        self.assertEqual("running", self.worker.states["dns"].state)
        self.assertEqual(count, self.dns.call_count)
        with patch("tools.mini_services.interfaces_snapshot") as scan:
            self.worker.tick()
            scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
