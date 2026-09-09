import copy
import ipaddress
import subprocess
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.mini_services import (
    DEFAULT_CONFIG,
    DHCP_DISCOVER,
    DHCP_HEADER,
    DHCP_MAGIC,
    DHCP_OFFER,
    DNS_TYPES,
    DhcpService,
    DnsService,
    blocklist_path,
    parse_blocklist_text,
    parse_dhcp_options,
    parse_dns_query,
    refresh_blocklists,
    validate_config,
)
from simpleoffice_network_boot import (
    DEFAULT_BOOT_SETTINGS,
    pxe_dhcp_values,
    render_ipxe,
    save_boot_settings,
    validate_boot_settings,
)
from simpleoffice_network_gateway import validate_gateway_settings


def dns_name(name: str) -> bytes:
    result = bytearray()
    for label in name.split("."):
        raw = label.encode("ascii")
        result.extend((len(raw),))
        result.extend(raw)
    result.append(0)
    return bytes(result)


def dns_query(name: str, qtype: int = 1, ident: int = 0x1234) -> bytes:
    return struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0) + dns_name(name) + struct.pack("!HH", qtype, 1)


def dhcp_discover(mac: bytes, xid: int = 0x12345678) -> bytes:
    chaddr = mac + b"\0" * (16 - len(mac))
    header = DHCP_HEADER.pack(
        1, 1, len(mac), 0, xid, 0, 0x8000,
        b"\0" * 4, b"\0" * 4, b"\0" * 4, b"\0" * 4,
        chaddr, b"", b"",
    )
    return header + DHCP_MAGIC + bytes((53, 1, DHCP_DISCOVER, 12, 4)) + b"test" + bytes((255,))


class MiniServicesConfigTests(unittest.TestCase):
    def test_default_config_is_valid(self):
        config = validate_config(copy.deepcopy(DEFAULT_CONFIG))
        self.assertEqual("192.168.178.0/24", config["dhcp"]["network"])
        self.assertEqual(["1.1.1.1", "9.9.9.9"], config["dns"]["upstreams"])
        self.assertEqual(["127.0.0.1"], config["dns"]["bind"])

    def test_dns_rejects_unspecified_bind_addresses(self):
        for bind in ("0.0.0.0", "::"):
            with self.subTest(bind=bind):
                config = copy.deepcopy(DEFAULT_CONFIG)
                config["dns"]["bind"] = [bind]
                with self.assertRaisesRegex(ValueError, "nicht alle Netzwerkinterfaces"):
                    validate_config(config)

    def test_rejects_invalid_dhcp_timer_order(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["dhcp"]["renewal_time"] = 80000
        config["dhcp"]["rebinding_time"] = 70000
        with self.assertRaisesRegex(ValueError, "T1 < T2 < Lease"):
            validate_config(config)

    def test_rejects_pool_outside_network(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["dhcp"]["pool_end"] = "192.168.179.20"
        with self.assertRaisesRegex(ValueError, "Pool"):
            validate_config(config)

    def test_reservations_require_unique_ips(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["dhcp"]["reservations"] = [
            {"mac": "00:11:22:33:44:55", "ip": "192.168.178.10"},
            {"mac": "00:11:22:33:44:66", "ip": "192.168.178.10"},
        ]
        with self.assertRaisesRegex(ValueError, "mehrfach reserviert"):
            validate_config(config)

    def test_blocklist_parser_accepts_hosts_and_adblock_syntax(self):
        domains = parse_blocklist_text(
            "# comment\n0.0.0.0 ads.example\n127.0.0.1 tracker.example\n||banner.example^\n"
        )
        self.assertEqual({"ads.example", "tracker.example", "banner.example"}, domains)

    def test_rejects_malformed_or_oversized_custom_dhcp_options(self):
        invalid_values = ("hex:zz", "base64:***", "ip:not-an-ip", "u32:-1", "text:" + "x" * 256)
        for value in invalid_values:
            with self.subTest(value=value[:20]):
                config = copy.deepcopy(DEFAULT_CONFIG)
                config["dhcp"]["custom_options"] = {"123": value}
                with self.assertRaisesRegex(ValueError, r"DHCP-Option 123"):
                    validate_config(config)

    def test_failed_refresh_preserves_last_active_blocklist(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def geturl(self): return "https://lists.example/block.txt"
            def read(self, _limit): return b"0.0.0.0 ads.example\n"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini-services.json"
            config = copy.deepcopy(DEFAULT_CONFIG)
            config["dns"]["blocklist_urls"] = ["https://lists.example/block.txt"]
            with mock.patch("simpleoffice_mini_services._https_open", return_value=Response()):
                first = refresh_blocklists(config, path)
            self.assertEqual(1, first["domains"])
            with mock.patch("simpleoffice_mini_services._https_open", side_effect=OSError("offline")):
                second = refresh_blocklists(config, path)
            self.assertTrue(second["preserved_previous"])
            self.assertEqual(1, second["domains"])
            self.assertEqual("ads.example\n", blocklist_path(path).read_text(encoding="utf-8"))


class MiniDnsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.config_path = Path(self.temp.name) / "mini-services.json"
    def tearDown(self): self.temp.cleanup()

    def test_local_a_record_redirects_without_upstream(self):
        config = copy.deepcopy(DEFAULT_CONFIG["dns"])
        config["records"] = [{"name": "service.home.arpa", "type": "A", "value": "192.168.178.7", "ttl": 120}]
        service = DnsService(validate_config({"dhcp": DEFAULT_CONFIG["dhcp"], "dns": config})["dns"], self.config_path)
        query = dns_query("service.home.arpa"); response = service.resolve(query, "192.168.178.20", tcp_client=False)
        self.assertIsNotNone(response); self.assertEqual(query[:2], response[:2]); self.assertEqual(1, struct.unpack_from("!H", response, 6)[0])
        self.assertIn(ipaddress.ip_address("192.168.178.7").packed, response)

    def test_manual_block_zeroes_a_record(self):
        config = copy.deepcopy(DEFAULT_CONFIG["dns"]); config["manual_blocks"] = ["ads.example"]
        service = DnsService(validate_config({"dhcp": DEFAULT_CONFIG["dhcp"], "dns": config})["dns"], self.config_path)
        response = service.resolve(dns_query("ads.example"), "192.168.178.20", tcp_client=False)
        self.assertIsNotNone(response); self.assertEqual(1, struct.unpack_from("!H", response, 6)[0]); self.assertTrue(response.endswith(b"\x00\x00\x00\x00"))

    def test_allowlist_wins_over_manual_block(self):
        config = copy.deepcopy(DEFAULT_CONFIG["dns"]); config["manual_blocks"] = ["*.example"]; config["allowlist"] = ["good.example"]
        config["records"] = [{"name": "good.example", "type": "A", "value": "10.0.0.5", "ttl": 60}]
        service = DnsService(validate_config({"dhcp": DEFAULT_CONFIG["dhcp"], "dns": config})["dns"], self.config_path)
        response = service.resolve(dns_query("good.example"), "10.0.0.2", tcp_client=False)
        self.assertIn(ipaddress.ip_address("10.0.0.5").packed, response)

    def test_dns_query_parser_detects_question(self):
        parsed = parse_dns_query(dns_query("host.home.arpa", DNS_TYPES["AAAA"]))
        self.assertEqual("host.home.arpa", parsed["name"]); self.assertEqual(DNS_TYPES["AAAA"], parsed["qtype"])


class MiniDhcpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.config_path = Path(self.temp.name) / "mini-services.json"
    def tearDown(self): self.temp.cleanup()

    def test_discover_receives_offer_with_rfc_options(self):
        config = validate_config(copy.deepcopy(DEFAULT_CONFIG))["dhcp"]
        service = DhcpService(config, self.config_path)
        result = service.handle_packet(dhcp_discover(bytes.fromhex("001122334455")), ("0.0.0.0", 68))
        self.assertIsNotNone(result)
        response, destination = result; values = DHCP_HEADER.unpack_from(response); offered = str(ipaddress.ip_address(values[8]))
        self.assertTrue(ipaddress.ip_address(offered) in ipaddress.ip_network(config["network"]))
        options = parse_dhcp_options(response[DHCP_HEADER.size :])
        self.assertEqual(bytes((DHCP_OFFER,)), options[53]); self.assertEqual(ipaddress.ip_address(config["server_ip"]).packed, options[54])
        self.assertIn(1, options); self.assertIn(3, options); self.assertIn(6, options); self.assertIn(51, options)
        self.assertEqual(("255.255.255.255", 68), destination)

    def test_reserved_mac_gets_reserved_ip(self):
        full = copy.deepcopy(DEFAULT_CONFIG)
        full["dhcp"]["reservations"] = [{"mac": "00:11:22:33:44:55", "ip": "192.168.178.10", "hostname": "printer"}]
        service = DhcpService(validate_config(full)["dhcp"], self.config_path)
        response, _destination = service.handle_packet(dhcp_discover(bytes.fromhex("001122334455")), ("0.0.0.0", 68))
        self.assertEqual("192.168.178.10", str(ipaddress.ip_address(DHCP_HEADER.unpack_from(response)[8])))


class NetworkBootTests(unittest.TestCase):
    def test_boot_settings_keep_serving_disabled_by_default(self):
        settings = validate_boot_settings(copy.deepcopy(DEFAULT_BOOT_SETTINGS))
        self.assertFalse(settings["enabled"])
        self.assertFalse(settings["tftp_enabled"])
        self.assertEqual("127.0.0.1", settings["tftp_bind"])

    def test_tftp_rejects_unspecified_bind_address(self):
        settings = copy.deepcopy(DEFAULT_BOOT_SETTINGS)
        settings["tftp_bind"] = "0.0.0.0"
        with self.assertRaisesRegex(ValueError, "nicht alle Netzwerkinterfaces"):
            validate_boot_settings(settings)

    def test_pxe_architecture_and_ipxe_chain_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "mini-services.json"
            settings = copy.deepcopy(DEFAULT_BOOT_SETTINGS)
            settings.update({
                "enabled": True,
                "http_base_url": "http://192.168.50.1:8080",
                "default_profile": "linux",
                "profiles": [{"id": "linux", "label": "Linux", "mode": "kernel", "kernel": "linux/vmlinuz", "initrd": "linux/initrd.img"}],
            })
            save_boot_settings(settings, config_path)
            _server, bios = pxe_dhcp_values({93: struct.pack("!H", 0)}, config_path)
            _server, uefi = pxe_dhcp_values({93: struct.pack("!H", 9)}, config_path)
            _server, ipxe = pxe_dhcp_values({60: b"iPXE"}, config_path)
            self.assertEqual("undionly.kpxe", bios)
            self.assertEqual("ipxe.efi", uefi)
            self.assertIn("/network-boot/ipxe?profile=linux", ipxe)
            script = render_ipxe("linux", config_path)
            self.assertIn("kernel http://192.168.50.1:8080/network-boot/files/linux/vmlinuz", script)
            self.assertIn("initrd http://192.168.50.1:8080/network-boot/files/linux/initrd.img", script)

    def test_gateway_modes_are_explicit(self):
        nat = validate_gateway_settings({"enabled": True, "mode": "nat", "internal_network": "192.168.50.0/24"})
        routed = validate_gateway_settings({"enabled": True, "mode": "route", "internal_network": "192.168.50.0/24"})
        self.assertEqual("nat", nat["mode"]); self.assertEqual("route", routed["mode"])

    def test_peer_role_names_are_peer_perspective_and_separate(self):
        root = Path(__file__).resolve().parents[1]
        module = (root / "app" / "network_boot_federation.py").read_text(encoding="utf-8")
        template = (root / "templates" / "admin" / "network_boot_federation.html").read_text(encoding="utf-8")
        self.assertIn('POLICY_OFFERS = "offers_network_boot"', module)
        self.assertIn('POLICY_STORES = "stores_network_boot"', module)
        self.assertIn("Networkboot anbieten", template)
        self.assertIn("Networkboot-Daten vorhalten", template)
        self.assertIn("Von diesem Peer holen", template)
        self.assertIn("Zu diesem Peer spiegeln", template)


class MiniServicesPackagingTests(unittest.TestCase):
    def test_worker_import_does_not_initialize_flask_package(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-c", "import sys; import tools.mini_services; raise SystemExit(1 if 'app' in sys.modules else 0)"],
            cwd=root, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_systemd_worker_has_scoped_network_capabilities(self):
        root = Path(__file__).resolve().parents[1]
        unit = (root / "packaging" / "simpleoffice-mini-services.service").read_text(encoding="utf-8")
        self.assertIn("User=simpleoffice", unit)
        self.assertIn("AmbientCapabilities=CAP_NET_BIND_SERVICE CAP_NET_RAW CAP_NET_ADMIN", unit)
        self.assertIn("CapabilityBoundingSet=CAP_NET_BIND_SERVICE CAP_NET_RAW CAP_NET_ADMIN", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectKernelTunables=false", unit)
        self.assertIn("AF_NETLINK", unit)

    def test_package_installs_and_enables_worker_unit(self):
        root = Path(__file__).resolve().parents[1]
        builder = (root / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        postinst = (root / "packaging" / "postinst.sh").read_text(encoding="utf-8")
        self.assertIn("simpleoffice-mini-services.service", builder)
        self.assertIn("enable simpleoffice-mini-services.service", postinst)
        self.assertIn("restart simpleoffice-mini-services.service", postinst)


if __name__ == "__main__":
    unittest.main()
