import json
import unittest
from unittest.mock import patch
from simpleoffice_network_gateway import interfaces_snapshot, binding_available


class LinuxInventoryTests(unittest.TestCase):
    def scan(self, data, routes=None):
        with patch("simpleoffice_network_gateway.platform_kind", return_value="linux"), patch("simpleoffice_network_gateway._run", side_effect=[{"ok": True, "stdout": json.dumps(data)}, {"ok": True, "stdout": json.dumps(routes)}]):
            return interfaces_snapshot()

    def test_malformed_inventory_does_not_claim_address_loss(self):
        cases = [None, {}, [None], [{"ifname": "lan", "addr_info": None}],
                 [{"ifname": "lan", "addr_info": [None]}]]
        address = {"family": "inet6", "local": "2001:db8::1", "prefixlen": 64}
        for field, value in (("local", "invalid"), ("prefixlen", 999), ("family", "inet"), ("flags", [None])):
            cases.append([{"ifname": "lan", "addr_info": [{**address, field: value}]}])
        for data in cases:
            with self.subTest(data=data):
                snapshot = self.scan(data)
                self.assertFalse(snapshot["available"])
                self.assertTrue(binding_available({"bind": "2001:db8::1"}, snapshot, addresses=("bind",)))

    def test_empty_valid_inventory_is_confirmed_loss(self):
        snapshot = self.scan([])
        self.assertTrue(snapshot["available"])
        self.assertFalse(binding_available({"bind": "2001:db8::1"}, snapshot, addresses=("bind",)))

    def test_invalid_route_shape_does_not_discard_valid_addresses(self):
        snapshot = self.scan([{"ifname": "lan", "addr_info": [{"family": "inet6", "local": "2001:0db8::1", "prefixlen": 64}]}])
        self.assertTrue(snapshot["available"])
        self.assertEqual([], snapshot["default_interfaces"])
        self.assertTrue(binding_available({"bind": "2001:db8::1"}, snapshot, addresses=("bind",)))
