import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_rendezvous_messages import FederationRendezvousMessages
from app.federation_store import FederationStore


class FederationRendezvousMessagesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="federation-rendezvous-test")
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.store = FederationStore(self.root)
        self.store.save_peer("peer-a", "A", "https://a.example", "token-a", {}, True)
        self.store.save_peer("peer-b", "B", "https://b.example", "token-b", {}, True)
        self.messages = FederationRendezvousMessages(self.root)

    def test_signal_is_delivered_once_to_enabled_recipient(self):
        self.messages.send("peer-a", "peer-b", "connect", {"candidate": "opaque"})

        first = self.messages.receive("peer-b")
        second = self.messages.receive("peer-b")

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["sender_peer"], "peer-a")
        self.assertEqual(first[0]["payload"], {"candidate": "opaque"})
        self.assertEqual(second, [])

    def test_unknown_or_disabled_recipient_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "recipient is not enabled"):
            self.messages.send("peer-a", "unknown-peer", "connect", {})

        self.store.save_peer("peer-b", "B", "https://b.example", "", {}, False)
        with self.assertRaisesRegex(ValueError, "recipient is not enabled"):
            self.messages.send("peer-a", "peer-b", "connect", {})

    def test_payload_size_is_bounded_in_encoded_bytes(self):
        with self.assertRaisesRegex(ValueError, "payload is too large"):
            self.messages.send("peer-a", "peer-b", "connect", {"value": "x" * 16385})

        with self.assertRaisesRegex(ValueError, "payload is too large"):
            self.messages.send("peer-a", "peer-b", "connect", {"value": "\u20ac" * 6000})

    def test_pending_limit_is_per_sender_and_claimed_messages_release_capacity(self):
        with patch("app.federation_rendezvous_messages.MAX_PENDING_PER_SENDER", 1):
            self.messages.send("peer-a", "peer-b", "connect", {"sequence": 1})
            with self.assertRaisesRegex(ValueError, "sender queue is full"):
                self.messages.send("peer-a", "peer-b", "connect", {"sequence": 2})

            self.messages.receive("peer-b")
            self.messages.send("peer-a", "peer-b", "connect", {"sequence": 3})


if __name__ == "__main__":
    unittest.main()
