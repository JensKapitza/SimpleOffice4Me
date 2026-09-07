import hashlib
import time
import unittest

from app.federation_print_auth import PrintRequestProof, identify_source_peer, sign_request


class FakeFederationStore:
    def __init__(self, peers, tokens):
        self._peers = peers
        self._tokens = tokens

    def list_peers(self):
        return list(self._peers)

    def peer_token(self, peer_id):
        return self._tokens[peer_id]


def proof_values():
    return {
        "timestamp": int(time.time()),
        "nonce": "nonce-1234567890abcdef",
        "printer_id": "printer-1",
        "policy_revision": "a" * 64,
        "retention_ceiling": "no_store",
        "ttl_ceiling_seconds": 0,
        "content_type": "application/pdf",
        "filename": "test.pdf",
        "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
        "payload_size": 7,
    }


def peer(peer_id, *, receive, enabled=True):
    return {
        "peer_id": peer_id,
        "enabled": enabled,
        "policy": {"printing": {"receive": receive}},
    }


class FederationPrintAuthTest(unittest.TestCase):
    def test_unique_allowed_peer_is_derived_from_credential(self):
        values = proof_values()
        token = "unique-source-token"
        signature = sign_request(PrintRequestProof(peer_id="source-a", **values), token)
        store = FakeFederationStore(
            [peer("source-a", receive=True), peer("blocked", receive=False)],
            {"source-a": token, "blocked": "other-token"},
        )
        peer_id, proof = identify_source_peer(store, values, signature)
        self.assertEqual(peer_id, "source-a")
        self.assertEqual(proof.peer_id, "source-a")

    def test_token_reused_by_blocked_peer_is_rejected(self):
        values = proof_values()
        shared = "must-not-be-reused"
        signature = sign_request(PrintRequestProof(peer_id="source-a", **values), shared)
        store = FakeFederationStore(
            [peer("source-a", receive=True), peer("blocked", receive=False)],
            {"source-a": shared, "blocked": shared},
        )
        with self.assertRaisesRegex(ValueError, "mehrfach verwendet"):
            identify_source_peer(store, values, signature)

    def test_receive_disabled_peer_cannot_authenticate_even_with_valid_own_signature(self):
        values = proof_values()
        token = "blocked-source-token"
        signature = sign_request(PrintRequestProof(peer_id="blocked", **values), token)
        store = FakeFederationStore(
            [peer("source-a", receive=True), peer("blocked", receive=False)],
            {"source-a": "allowed-token", "blocked": token},
        )
        with self.assertRaisesRegex(ValueError, "nicht eindeutig"):
            identify_source_peer(store, values, signature)


if __name__ == "__main__":
    unittest.main()
