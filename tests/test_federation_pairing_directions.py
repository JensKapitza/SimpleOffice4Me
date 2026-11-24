"""Pairing direction lab: actual signed routes, isolated roots, in-process transport."""
from __future__ import annotations

import ipaddress
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from flask import Flask, current_app

from app.federation_discovery_http import bp
from app.federation_local_profile import local_profile
from app.federation_pairing_client import PairingClientError, remote_pairing_status
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore


class InProcessConnection:
    """Substitute the socket with a Flask test client; never open a network socket."""

    def __init__(self, remote, base_url):
        self.remote = remote
        self.base_url = base_url
        self.closed = False

    def request(self, method, path, *, body, headers):
        self.response = self.remote.test_client().open(
            path, method=method, base_url=self.base_url, data=body, headers=headers,
        )

    def getresponse(self):
        data = BytesIO(self.response.data)
        data.status = self.response.status_code
        return data

    def close(self):
        self.closed = True


class FederationPairingDirectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.nodes = {}
        self.profiles = {}
        self.addresses = {}
        patcher = patch("app.federation_local_profile._identity.peer_id",
                        side_effect=lambda: current_app.config["TEST_PAIRING_ID"])
        patcher.start()
        self.addCleanup(patcher.stop)
        for key, address in (("office-a", "192.0.2.10"), ("office-b", "192.0.2.20")):
            node = Flask(key)
            root = Path(directory.name) / key
            node.config.update(TESTING=True, SECRET_KEY=f"fixture-only-{key}",
                               DOCUMENT_ROOT=str(root), TEST_PAIRING_ID=key)
            node.register_blueprint(bp)
            self.nodes[key] = node
            self.addresses[key] = address
            with node.app_context():
                self.profiles[key] = local_profile(root, f"https://{key}.example.test")

        for local, remote in (("office-a", "office-b"), ("office-b", "office-a")):
            profile = self.profiles[remote]
            trust = self._trust(local)
            trust.remember(remote, "DE", profile["fingerprint"], "fixture", profile["public_key"])

    def _trust(self, local):
        return FederationTrustStore(self.nodes[local].config["DOCUMENT_ROOT"])

    def _query(self, local, remote):
        with self.nodes[local].app_context():
            return remote_pairing_status(current_app.config["DOCUMENT_ROOT"], self.profiles[remote])

    def _validate(self, value, *, allow_private):
        # The boundary only supplies fixture DNS values. Production validation
        # and no-network-on-denial are verified separately by the client tests.
        self.assertIsNone(allow_private)
        parsed = urlsplit(value)
        key = parsed.hostname.split(".")[0]
        return value, parsed, 443, [ipaddress.ip_address(self.addresses[key])]

    def test_outbound_success_does_not_establish_the_reverse_direction(self):
        connections = []
        reachable_origins = {"office-b"}

        def connect(scheme, address, port, host, timeout):
            self.assertEqual(("https", 443), (scheme, port))
            remote = host.split(".")[0]
            self.assertEqual(self.addresses[remote], address)
            if current_app.config["TEST_PAIRING_ID"] not in reachable_origins:
                raise ConnectionRefusedError("fixture one-way connection failure")
            channel = InProcessConnection(self.nodes[remote], self.profiles[remote]["base_url"])
            connections.append(channel)
            return channel

        with patch("app.federation_pairing_client._validated_target", side_effect=self._validate), patch(
            "app.federation_pairing_client._connection_for", side_effect=connect,
        ):
            self.assertEqual({"peer_id": "office-b", "status": "missing"}, self._query("office-b", "office-a"))
            with self.assertRaises(PairingClientError) as fault:
                self._query("office-a", "office-b")
            reachable_origins.add("office-a")
            self.assertEqual({"peer_id": "office-a", "status": "missing"}, self._query("office-a", "office-b"))

        self.assertEqual("transport", fault.exception.reason)
        self.assertEqual(4, len(connections))
        self.assertTrue(all(channel.closed for channel in connections))
        # A status request cannot enable either peer or establish trust.
        for local, remote in (("office-a", "office-b"), ("office-b", "office-a")):
            self.assertIsNone(self._trust(local).get_trust(remote))
            self.assertFalse(FederationStore(self.nodes[local].config["DOCUMENT_ROOT"]).get_peer(remote))


if __name__ == "__main__":
    unittest.main()
