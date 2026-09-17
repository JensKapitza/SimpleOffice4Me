"""Real IPv6 DNS sockets on loopback/free ports; no upstream or LAN traffic."""
import copy
import ipaddress
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simpleoffice_mini_core import DEFAULT_CONFIG
from simpleoffice_mini_runtime import DnsService
from simpleoffice_service_lifecycle import service_health
from tests.test_mini_services import dns_query, DNS_TYPES


class DnsIPv6LoopbackTests(unittest.TestCase):
    def test_udp_tcp_idempotence_and_restart_without_upstream(self):
        if not socket.has_ipv6:
            self.skipTest("IPv6 unavailable")
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
                probe.bind(("::1", 0))
        except OSError as exc:
            self.skipTest("IPv6 loopback unavailable: " + type(exc).__name__)
        with tempfile.TemporaryDirectory() as folder:
            config = copy.deepcopy(DEFAULT_CONFIG["dns"])
            config.update(bind=["::1"], port=0, records=[
                {"name": "service.home.arpa", "type": "AAAA", "value": "2001:db8::7", "ttl": 60}])
            service = DnsService(config, Path(folder) / "mini.json")
            self.addCleanup(service.stop)
            query = dns_query("service.home.arpa", DNS_TYPES["AAAA"])
            with patch.object(service, "_forward", side_effect=AssertionError("Unexpected upstream query")):
                for _ in range(2):
                    try:
                        service.start()
                        self.assertTrue(service_health(service))
                        sockets = list(service.sockets)
                        service.start()
                        self.assertEqual(sockets, service.sockets)
                        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as client:
                            client.settimeout(3)
                            client.sendto(query, sockets[0].getsockname())
                            response = client.recv(65535)
                            self.check_response(query, response)
                        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as client:
                            client.settimeout(3)
                            client.connect(sockets[1].getsockname())
                            client.sendall(struct.pack("!H", len(query)) + query)
                            length = struct.unpack("!H", self.read_exact(client, 2))[0]
                            self.check_response(query, self.read_exact(client, length))
                        threads = list(service.threads)
                    finally:
                        service.stop()
                    service.stop()
                    self.assertFalse(service_health(service))
                    self.assertTrue(all(sock.fileno() == -1 for sock in sockets))
                    self.assertTrue(all(not thread.is_alive() for thread in threads))

    def read_exact(self, sock, count):
        result = b""
        while len(result) < count:
            chunk = sock.recv(count - len(result))
            self.assertTrue(chunk, "TCP response ended early")
            result += chunk
        return result

    def check_response(self, query, response):
        self.assertEqual(query[:2], response[:2])
        self.assertEqual(1, struct.unpack_from("!H", response, 6)[0])
        self.assertEqual(0, response[3] & 15)
        self.assertTrue(response.endswith(ipaddress.IPv6Address("2001:db8::7").packed))
