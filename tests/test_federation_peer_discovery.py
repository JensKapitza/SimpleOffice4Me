import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.federation_discovery_email import email_hash
from app.federation_discovery_endpoint import (
    fetch_discovery_profile,
    normalize_endpoint,
    validate_discovery_endpoint,
)
from app.federation_discovery_lan import _targets, discover_lan, is_private_lan_ipv4, scan_ports
from app.federation_discovery_service import discover_direct
from app.federation_qr import decode_peer, encode_peer
from app.federation_rendezvous_store import FederationRendezvousStore
from app.federation_store import FederationStore


PROFILE = {
    "peer_id": "peer-a",
    "label": "Peer A",
    "base_url": "https://peer.example",
    "country": "DE",
    "fingerprint": "sha256:test",
    "capabilities": {"discovery": True},
}


class FederationPeerDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_email_lookup_is_case_insensitive(self):
        self.assertEqual(email_hash("User@Example.org"), email_hash(" user@example.org "))

    def test_endpoint_defaults_to_https(self):
        self.assertEqual(normalize_endpoint("peer.example"), "https://peer.example")

    def test_endpoint_rejects_non_base_url_components(self):
        for value in (
            "https://peer.example/admin",
            "https://peer.example?next=http://127.0.0.1",
            "https://peer.example#internal",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_endpoint(value)

    def test_direct_discovery_blocks_loopback_and_link_local(self):
        with self.assertRaises(ValueError):
            validate_discovery_endpoint("http://127.0.0.1:8080")
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS": "1"}, clear=False):
            with self.assertRaises(ValueError):
                validate_discovery_endpoint("http://169.254.169.254")

    def test_direct_discovery_private_target_requires_opt_in(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS", None)
            with self.assertRaises(ValueError):
                validate_discovery_endpoint("https://10.20.30.40")
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS": "1"}, clear=False):
            self.assertEqual(validate_discovery_endpoint("https://10.20.30.40"), "https://10.20.30.40")

    def test_direct_discovery_rejects_dns_resolution_to_link_local(self):
        resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]
        with patch("app.federation_discovery_endpoint.socket.getaddrinfo", return_value=resolved):
            with self.assertRaises(ValueError):
                validate_discovery_endpoint("https://peer.example")

    def test_discovery_fetch_pins_connection_to_validated_ip(self):
        resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        connection = Mock()
        response = Mock()
        response.status = 200
        response.read.return_value = json.dumps(PROFILE).encode("utf-8")
        connection.getresponse.return_value = response
        with patch("app.federation_discovery_endpoint.socket.getaddrinfo", return_value=resolved), patch(
            "app.federation_discovery_endpoint._connection_for", return_value=connection
        ) as connection_factory:
            profile = fetch_discovery_profile("https://peer.example", timeout=8)
        self.assertEqual(profile["peer_id"], "peer-a")
        connection_factory.assert_called_once_with("https", "8.8.8.8", 443, "peer.example", 8)
        request_args, request_kwargs = connection.request.call_args
        self.assertEqual(request_args, ("GET", "/.well-known/simpleoffice-federation"))
        self.assertEqual(request_kwargs["headers"]["Host"], "peer.example")
        connection.close.assert_called_once_with()

    def test_internal_lan_fetch_can_probe_private_but_not_link_local(self):
        connection = Mock()
        response = Mock()
        response.status = 200
        response.read.return_value = json.dumps(PROFILE).encode("utf-8")
        connection.getresponse.return_value = response
        with patch("app.federation_discovery_endpoint._connection_for", return_value=connection):
            profile = fetch_discovery_profile(
                "http://192.168.20.44:8080", timeout=.2, allow_private=True
            )
        self.assertEqual(profile["peer_id"], "peer-a")
        with self.assertRaises(ValueError):
            fetch_discovery_profile(
                "http://169.254.169.254:8080", timeout=.2, allow_private=True
            )

    def test_direct_discovery_uses_pinned_fetcher(self):
        with patch("app.federation_discovery_service.fetch_discovery_profile", return_value=PROFILE) as fetcher:
            peer = discover_direct(self.root, "https://peer.example")
        self.assertEqual(peer["peer_id"], "peer-a")
        fetcher.assert_called_once_with("https://peer.example", timeout=8)

    def test_lan_target_generation_stays_inside_local_24(self):
        targets = _targets(["192.168.50.23"], [8080])
        self.assertIn("http://192.168.50.1:8080", targets)
        self.assertIn("http://192.168.50.254:8080", targets)
        self.assertNotIn("http://192.168.50.23:8080", targets)
        self.assertFalse(any("192.168.51." in endpoint for endpoint in targets))
        self.assertEqual(len(targets), 253)

    def test_lan_scan_accepts_only_rfc1918_ipv4(self):
        for value in ("10.2.3.4", "172.16.1.1", "172.31.255.2", "192.168.1.2"):
            self.assertTrue(is_private_lan_ipv4(value), value)
        for value in ("8.8.8.8", "169.254.1.1", "127.0.0.1", "::1"):
            self.assertFalse(is_private_lan_ipv4(value), value)

    def test_lan_ports_are_bounded(self):
        with patch.dict(
            os.environ,
            {"SIMPLEOFFICE_FEDERATION_LAN_PORTS": "9090,9091,9092,9093,9094"},
            clear=False,
        ):
            ports = scan_ports()
        self.assertLessEqual(len(ports), 4)
        self.assertTrue(all(1 <= port <= 65535 for port in ports))

    def test_lan_scan_remembers_found_peer_disabled(self):
        profile = {
            **PROFILE,
            "base_url": "http://192.168.50.44:8080",
            "fingerprint": "",
        }
        with patch(
            "app.federation_discovery_lan._targets",
            return_value=["http://192.168.50.44:8080"],
        ), patch("app.federation_discovery_lan._probe", return_value=profile), patch(
            "app.federation_discovery_lan.local_peer_id", return_value="this-phone"
        ):
            result = discover_lan(
                self.root, addresses=["192.168.50.23"], ports=[8080]
            )
        self.assertEqual([item["peer_id"] for item in result["peers"]], ["peer-a"])
        stored = FederationStore(self.root).get_peer("peer-a")
        self.assertIsNotNone(stored)
        self.assertFalse(stored["enabled"])
        self.assertEqual(stored["base_url"], "http://192.168.50.44:8080")

    def test_qr_payload_roundtrip(self):
        decoded = decode_peer(encode_peer(PROFILE))
        self.assertEqual(decoded["peer_id"], "peer-a")
        self.assertEqual(decoded["fingerprint"], "sha256:test")

    def test_rendezvous_resolves_profile(self):
        store = FederationRendezvousStore(self.root)
        store.register("lookup-key", PROFILE, 300)
        rows = store.resolve("lookup-key")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["peer_id"], "peer-a")


if __name__ == "__main__":
    unittest.main()
