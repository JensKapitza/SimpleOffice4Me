from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.v3_federation import (
    FederationContract,
    capability_descriptor,
    negotiate,
    health_snapshot,
)


class V3FederationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.peer_id = "remote-peer"
        self.local_id = "local-peer"
        self.peers = FederationStore(self.root)
        self.peers.save_peer(
            self.peer_id,
            "Remote",
            "https://remote.example.test",
            "",
            policy={
                "data_classes": {
                    "documents": {"receive": True, "auto_accept": True},
                    "contacts": {"receive": False},
                }
            },
        )
        FederationTrustStore(self.root).set_trust(
            self.peer_id,
            trust_level="NORMAL",
            propagation="DIRECT_ONLY",
        )
        self.contract = FederationContract(self.root, self.local_id)
        self.contract.store.remember_capabilities(
            self.peer_id,
            {
                "envelope_versions": [1],
                "objects": {"documents": [1], "contacts": [1]},
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, **changes):
        value = {
            "message_id": "message-0001",
            "sender_instance": self.peer_id,
            "recipient_instance": self.local_id,
            "type": "documents",
            "schema_version": 1,
            "envelope_version": 1,
            "time": "2026-09-29T18:00:00Z",
            "entity": {"type": "document", "id": "doc-1"},
            "object_ref": "document:doc-1",
            "integrity": {"sha256": "a" * 64},
            "payload": {"operation": "offer"},
        }
        value.update(changes)
        return value

    def test_capability_negotiation_is_versioned_and_bounded(self):
        result = negotiate({
            "envelope_versions": [1, 2],
            "objects": {"documents": [1, 2], "unknown": [1]},
        })
        self.assertTrue(result["compatible"])
        self.assertEqual(1, result["envelope_version"])
        self.assertEqual([1], result["objects"]["documents"])
        self.assertNotIn("unknown", result["objects"])
        descriptor = capability_descriptor()
        self.assertTrue(descriptor["legacy_v1_parallel"])
        self.assertFalse(descriptor["trust"]["transitive_default"])

    def test_health_snapshot_reports_component_ready(self):
        snapshot = health_snapshot()
        self.assertEqual("healthy", snapshot["status"])
        self.assertEqual("federation_v3_ready", snapshot["code"])
        self.assertEqual(1, snapshot["metrics"]["envelope_version"])


    def test_receive_is_idempotent(self):
        first = self.contract.receive(self.envelope())
        second = self.contract.receive(self.envelope())
        self.assertEqual("accepted", first["status"])
        self.assertEqual("duplicate", second["status"])
        rows = self.contract.store.existing("message-0001")
        self.assertEqual("accepted", rows["status"])

    def test_same_message_id_with_changed_content_is_rejected(self):
        self.assertEqual("accepted", self.contract.receive(self.envelope())["status"])
        changed = self.envelope(payload={"operation": "changed"})
        result = self.contract.receive(changed)
        self.assertEqual("rejected", result["status"])
        self.assertEqual("replay_mismatch", result["error"])

    def test_unsupported_version_is_quarantined(self):
        result = self.contract.receive(self.envelope(schema_version=2))
        self.assertEqual("quarantined", result["status"])
        self.assertEqual("unsupported_object_version", result["error"])

    def test_disabled_data_class_is_rejected(self):
        result = self.contract.receive(self.envelope(
            message_id="message-0002",
            type="contacts",
        ))
        self.assertEqual("rejected", result["status"])
        self.assertEqual("data_class_disabled", result["error"])

    def test_relation_does_not_grant_transfer_trust(self):
        FederationTrustStore(self.root).set_trust(
            self.peer_id,
            trust_level="NONE",
            propagation="DIRECT_ONLY",
        )
        result = self.contract.receive(self.envelope(message_id="message-0003"))
        self.assertEqual("rejected", result["status"])
        self.assertEqual("direct_trust_required", result["error"])

    def test_capability_must_be_negotiated(self):
        self.contract.store.remember_capabilities(
            self.peer_id,
            {"envelope_versions": [1], "objects": {"contacts": [1]}},
        )
        result = self.contract.receive(self.envelope(message_id="message-0004"))
        self.assertEqual("quarantined", result["status"])
        self.assertEqual("capability_not_negotiated", result["error"])

    def test_no_auto_accept_goes_pending(self):
        self.peers.save_peer(
            self.peer_id,
            "Remote",
            "https://remote.example.test",
            "",
            policy={"data_classes": {"documents": {"receive": True}}},
        )
        result = self.contract.receive(self.envelope(message_id="message-0005"))
        self.assertEqual("pending", result["status"])


if __name__ == "__main__":
    unittest.main()
