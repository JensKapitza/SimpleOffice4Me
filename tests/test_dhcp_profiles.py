import copy
import tempfile
import unittest
from pathlib import Path

from simpleoffice_dhcp_profiles import (
    ProfileDhcpService,
    load_profiles,
    profile_leases_path,
    save_profiles,
    validate_profiles,
)
from simpleoffice_mini_core import DEFAULT_CONFIG, validate_config


class DhcpProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp.name) / "mini-services.json"
        self.primary = validate_config(copy.deepcopy(DEFAULT_CONFIG))["dhcp"]

    def tearDown(self):
        self.temp.cleanup()

    def test_multiple_interfaces_get_independent_networks(self):
        rows = validate_profiles([
            {
                "id": "lan2", "enabled": True, "interface": "eth1", "bind": "10.20.0.1",
                "server_ip": "10.20.0.1", "network": "10.20.0.0/24",
                "pool_start": "10.20.0.20", "pool_end": "10.20.0.200",
                "routers": ["10.20.0.1"], "dns_servers": ["10.20.0.1"],
            },
            {
                "id": "lan3", "enabled": True, "interface": "eth2", "bind": "10.30.0.1",
                "server_ip": "10.30.0.1", "network": "10.30.0.0/24",
                "pool_start": "10.30.0.20", "pool_end": "10.30.0.200",
                "routers": ["10.30.0.1"], "dns_servers": ["10.30.0.1"],
            },
        ], self.primary)
        self.assertEqual(["eth1", "eth2"], [row["interface"] for row in rows])
        self.assertEqual(["10.20.0.0/24", "10.30.0.0/24"], [row["network"] for row in rows])

    def test_enabled_profile_requires_interface(self):
        with self.assertRaisesRegex(ValueError, "Interface"):
            validate_profiles([{
                "id": "lan2", "enabled": True, "bind": "10.20.0.1",
                "server_ip": "10.20.0.1", "network": "10.20.0.0/24",
                "pool_start": "10.20.0.20", "pool_end": "10.20.0.200",
            }], self.primary)

    def test_active_profiles_require_unique_interfaces(self):
        row = {
            "enabled": True, "interface": "eth1", "bind": "0.0.0.0",
            "server_ip": "10.20.0.1", "network": "10.20.0.0/24",
            "pool_start": "10.20.0.20", "pool_end": "10.20.0.200",
        }
        second = {
            **row, "id": "lan3", "server_ip": "10.21.0.1", "network": "10.21.0.0/24",
            "pool_start": "10.21.0.20", "pool_end": "10.21.0.200",
        }
        with self.assertRaisesRegex(ValueError, "bereits ein DHCP-Server"):
            validate_profiles([{**row, "id": "lan2"}, second], self.primary)

    def test_primary_without_interface_blocks_additional_active_server(self):
        primary = {**self.primary, "enabled": True, "interface": "", "bind": "0.0.0.0"}
        with self.assertRaisesRegex(ValueError, "Haupt-DHCP"):
            validate_profiles([{
                "id": "lan2", "enabled": True, "interface": "eth1", "bind": "0.0.0.0",
                "server_ip": "10.20.0.1", "network": "10.20.0.0/24",
                "pool_start": "10.20.0.20", "pool_end": "10.20.0.200",
            }], primary)

    def test_profile_leases_are_isolated(self):
        one = profile_leases_path(self.config_path, "lan2")
        two = profile_leases_path(self.config_path, "lan3")
        self.assertNotEqual(one, two)
        config = {
            **self.primary,
            "enabled": False,
            "interface": "eth1",
            "bind": "10.20.0.1",
            "server_ip": "10.20.0.1",
            "network": "10.20.0.0/24",
            "pool_start": "10.20.0.20",
            "pool_end": "10.20.0.200",
            "routers": ["10.20.0.1"],
            "dns_servers": ["10.20.0.1"],
        }
        service = ProfileDhcpService(validate_profiles([{**config, "id": "lan2"}], self.primary)[0], self.config_path, "lan2")
        self.assertEqual(one, service.leases.path)

    def test_profiles_roundtrip_separate_file(self):
        rows = [{
            "id": "lan2", "enabled": False, "interface": "eth1", "bind": "10.20.0.1",
            "server_ip": "10.20.0.1", "network": "10.20.0.0/24",
            "pool_start": "10.20.0.20", "pool_end": "10.20.0.200",
        }]
        save_profiles(rows, self.config_path, self.primary)
        loaded = load_profiles(self.config_path, self.primary)
        self.assertEqual("lan2", loaded[0]["id"])
        self.assertEqual("eth1", loaded[0]["interface"])

    def test_management_ui_contains_profile_fields_and_interface_choices(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "templates" / "admin" / "dhcp_profiles.html").read_text(encoding="utf-8")
        partial = (root / "templates" / "admin" / "_dhcp_profiles.html").read_text(encoding="utf-8")
        self.assertIn("DHCP-Netze pro Interface", page)
        self.assertIn("network-interface-list", page)
        self.assertIn('name="dhcp_profile_interface"', partial)
        self.assertIn('name="dhcp_profile_network"', partial)
        self.assertIn('name="dhcp_profile_pool_start"', partial)
        self.assertIn('name="dhcp_profile_pool_end"', partial)


if __name__ == "__main__":
    unittest.main()
