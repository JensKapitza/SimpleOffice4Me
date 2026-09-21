import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.network_system_status import dhcp_network_candidates, ipv4_routing_status, network_interfaces


class NetworkUiDiagnosticsTests(unittest.TestCase):
    def test_interfaces_include_ipv4_prefixes(self):
        payload = [{
            "ifindex": 2,
            "ifname": "eth0",
            "operstate": "UP",
            "addr_info": [{"family": "inet", "local": "192.168.50.10", "prefixlen": 24}],
        }]
        result = Mock(returncode=0, stdout=json.dumps(payload))
        with patch("app.network_system_status.socket.if_nameindex", return_value=[(2, "eth0")]), patch(
            "app.network_system_status.platform.system", return_value="Linux"
        ), patch("simpleoffice_network_gateway.platform_kind", return_value="linux"), patch(
            "simpleoffice_network_gateway._run", return_value={"ok": True, "stdout": result.stdout}
        ):
            rows = network_interfaces()
        self.assertEqual(["192.168.50.10/24"], rows[0]["ipv4"])
        self.assertEqual("up", rows[0]["state"])

    def test_interfaces_survive_missing_socket_if_nameindex(self):
        payload = [{
            "ifindex": 7,
            "ifname": "Ethernet",
            "operstate": "UP",
            "addr_info": [{"family": "inet", "local": "192.168.60.10", "prefixlen": 24}],
        }]
        result = Mock(returncode=0, stdout=json.dumps(payload))
        with patch(
            "app.network_system_status.socket.if_nameindex",
            side_effect=AttributeError("if_nameindex unavailable"),
            create=True,
        ), patch("app.network_system_status.platform.system", return_value="Linux"), patch(
            "simpleoffice_network_gateway.platform_kind", return_value="linux"
        ), patch(
            "simpleoffice_network_gateway._run", return_value={"ok": True, "stdout": result.stdout}
        ):
            rows = network_interfaces()
        self.assertEqual("Ethernet", rows[0]["name"])
        self.assertEqual(["192.168.60.10/24"], rows[0]["ipv4"])
        self.assertEqual("up", rows[0]["state"])

    def test_routing_status_reports_enabled(self):
        with patch("app.network_system_status.platform.system", return_value="Linux"), patch(
            "app.network_system_status.Path.read_text", return_value="1"
        ):
            status = ipv4_routing_status()
        self.assertTrue(status["known"])
        self.assertTrue(status["enabled"])

    def test_template_keeps_freetext_and_detected_interface_choices(self):
        template = (Path(__file__).resolve().parents[1] / "templates" / "admin" / "mini_services.html").read_text(encoding="utf-8")
        self.assertIn('list="network-interface-list"', template)
        self.assertIn('datalist id="network-interface-list"', template)
        self.assertIn("Freitext bleibt möglich", template)
        self.assertIn("IPv4-Forwarding", template)

    def test_dhcp_candidates_use_detected_ipv4_only_and_never_guess_pool(self):
        rows = [
            {"name": "eth0", "state": "up", "ipv4": ["192.168.50.10/24", "fe80::1/64"]},
            {"name": "wlan0", "state": "up", "ipv4": ["10.20.30.5/24"]},
            {"name": "lo", "state": "up", "ipv4": ["127.0.0.1/8"]},
            {"name": "bad", "state": "up", "ipv4": ["not-an-address"]},
        ]
        candidates = dhcp_network_candidates(rows, "192.168.50.0/24", "192.168.50.10")

        self.assertEqual(
            [
                {
                    "interface": "eth0",
                    "address": "192.168.50.10",
                    "network": "192.168.50.0/24",
                    "selected": True,
                },
                {
                    "interface": "wlan0",
                    "address": "10.20.30.5",
                    "network": "10.20.30.0/24",
                    "selected": False,
                },
            ],
            candidates,
        )
        self.assertTrue(all("pool" not in key for row in candidates for key in row))

    def test_template_requires_explicit_click_before_candidate_values_are_used(self):
        template = (Path(__file__).resolve().parents[1] / "templates" / "admin" / "mini_services.html").read_text(encoding="utf-8")
        self.assertIn('type="button"', template)
        self.assertIn("dhcp-network-choice", template)
        self.assertIn("Nichts wird automatisch aktiviert", template)
        self.assertIn("button.addEventListener('click'", template)
        self.assertNotIn("dhcp-enabled.checked", template)


if __name__ == "__main__":
    unittest.main()
