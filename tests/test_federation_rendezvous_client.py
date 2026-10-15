import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_rendezvous_client import receive_signals, send_signal
from app.federation_store import FederationStore


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


class FederationRendezvousClientTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="rendezvous-client-test")
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.store = FederationStore(self.root)

    def save_relay(self, policy, *, enabled=True):
        self.store.save_peer(
            "relay-peer",
            "Relay Peer",
            "https://relay.example.test",
            "relay-token",
            policy,
            enabled,
        )

    def test_active_peer_is_denied_without_explicit_relay_opt_in(self):
        self.save_relay({})

        with patch("app.federation_rendezvous_client._request") as request:
            with self.assertRaisesRegex(ValueError, "not explicitly enabled"):
                send_signal(self.root, "relay-peer", "recipient", {"offer": "opaque"})

        request.assert_not_called()

    def test_only_boolean_true_enables_relay(self):
        self.save_relay({"rendezvous_relay": {"enabled": "true"}})

        with patch("app.federation_rendezvous_client._request") as request:
            with self.assertRaisesRegex(ValueError, "not explicitly enabled"):
                receive_signals(self.root, "relay-peer")

        request.assert_not_called()

    def test_opted_in_relay_can_receive_signals(self):
        self.save_relay({"rendezvous_relay": {"enabled": True}})
        response = _Response({"messages": [{"message_id": "message-1"}]})

        with patch(
            "app.federation_rendezvous_client.local_peer_id",
            return_value="local-peer",
        ), patch(
            "app.federation_rendezvous_client._request",
            return_value=response,
        ) as request:
            messages = receive_signals(self.root, "relay-peer")

        self.assertEqual([{"message_id": "message-1"}], messages)
        self.assertEqual(
            "https://relay.example.test/federation/v1/discovery/signal",
            request.call_args.args[0],
        )

    def test_disabled_peer_stays_denied_after_relay_opt_in(self):
        self.save_relay({"rendezvous_relay": {"enabled": True}}, enabled=False)

        with patch("app.federation_rendezvous_client._request") as request:
            with self.assertRaisesRegex(ValueError, "peer is not enabled"):
                receive_signals(self.root, "relay-peer")

        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
