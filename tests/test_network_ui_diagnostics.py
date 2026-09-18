import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.network_system_status import ipv4_routing_status, network_interfaces


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


if __name__ == "__main__":
    unittest.main()
