import copy
import json
import unittest
from unittest.mock import patch
from simpleoffice_network_gateway import interfaces_snapshot, binding_available


class WindowsInventoryTests(unittest.TestCase):
    def setUp(self):
        self.data = {"interfaces": [{"InterfaceAlias": "vEthernet", "InterfaceIndex": 7, "Status": "Up"}],
                     "addresses": [{"InterfaceIndex": 7, "IPAddress": "fe80::1", "PrefixLength": 64, "State": "Preferred"}]}

    def scan(self, data):
        with patch("simpleoffice_network_gateway.platform_kind", return_value="windows"), patch("simpleoffice_network_gateway._powershell", side_effect=[{"ok": True, "stdout": json.dumps(data)}, {"ok": True, "stdout": "0"}]) as run:
            result = interfaces_snapshot()
            self.assertIn("Get-NetIPConfiguration -All -ErrorAction Stop", run.call_args_list[0].args[0])
            self.assertIn("Get-NetIPAddress -ErrorAction Stop", run.call_args_list[0].args[0])
            self.assertEqual(3, run.call_args_list[0].args[1])
            return result

    def test_ipv6_scope_and_confirmed_removal(self):
        snapshot = self.scan(self.data)
        self.assertTrue(snapshot["available"])
        self.assertEqual([4, 6], snapshot["address_families"])
        self.assertTrue(binding_available({"bind": "fe80::1%7"}, snapshot, addresses=("bind",)))
        self.assertFalse(binding_available({"bind": "fe80::1%8"}, snapshot, addresses=("bind",)))
        self.data["addresses"] = []
        self.assertFalse(binding_available({"bind": "fe80::1%7"}, self.scan(self.data), addresses=("bind",)))

    def test_invalid_dad_states_excluded_and_deprecated_binding_retained(self):
        for state, present in (("Tentative", False), ("Duplicate", False), ("Invalid", False), ("Deprecated", True)):
            self.data["addresses"][0]["State"] = state
            self.assertEqual(present, bool(self.scan(self.data)["interfaces"][0]["addresses"]))

    def test_partial_or_malformed_inventory_does_not_claim_address_loss(self):
        cases = [None, {"interfaces": []}, {"interfaces": [], "addresses": self.data["addresses"]},
                 {"interfaces": None, "addresses": []}]
        for key, val in (("IPAddress", "invalid"), ("State", "unknown"), ("PrefixLength", 999)):
            changed = copy.deepcopy(self.data)
            changed["addresses"][0][key] = val
            cases.append(changed)
        for data in cases:
            with self.subTest(data=data):
                snapshot = self.scan(data)
                self.assertFalse(snapshot["available"])
                self.assertTrue(binding_available({"bind": "fe80::1%7"}, snapshot, addresses=("bind",)))

    def test_disconnected_interface_cannot_supply_a_binding(self):
        self.data["interfaces"][0]["Status"] = "Disconnected"
        self.assertFalse(binding_available({"bind": "fe80::1%7"}, self.scan(self.data), addresses=("bind",)))
