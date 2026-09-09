import time
import unittest

from app.gamification_federation import (
    GameRequestProof,
    authenticate_peer,
    body_digest,
    minimal_challenge_envelope,
    parse_request_proof,
    peer_allows,
    sign_request,
    signed_headers,
)


class FakeFederationStore:
    def __init__(self, peers, tokens):
        self.peers = {item["peer_id"]: item for item in peers}
        self.tokens = dict(tokens)
        self.nonces = set()

    def get_peer(self, peer_id):
        return self.peers.get(peer_id)

    def list_peers(self):
        return list(self.peers.values())

    def peer_token(self, peer_id):
        return self.tokens[peer_id]

    def claim_nonce(self, nonce, expires_at):
        if nonce in self.nonces or expires_at < int(time.time()):
            return False
        self.nonces.add(nonce)
        return True


def peer(peer_id="friend", **game):
    return {
        "peer_id": peer_id,
        "enabled": True,
        "policy": {"gamification": {
            "receive_challenges": True,
            "submit_answers": True,
            "preview_media": True,
            "providers": ["images", "documents", "contacts"],
            **game,
        }},
    }


class GamificationFederationTests(unittest.TestCase):
    def test_signed_request_roundtrip_and_replay_rejected(self):
        store = FakeFederationStore([peer()], {"friend": "unique-secret"})
        path = "/federation/v1/gamification/sessions/s1/next"
        headers = signed_headers(
            "friend", "unique-secret", method="GET", path=path,
            action="fetch_challenge",
        )
        found, proof = authenticate_peer(
            store, headers, method="GET", path=path, body=b"",
            required_permission="receive_challenges",
        )
        self.assertEqual(found["peer_id"], "friend")
        self.assertEqual(proof.action, "fetch_challenge")
        with self.assertRaises(ValueError):
            authenticate_peer(
                store, headers, method="GET", path=path, body=b"",
                required_permission="receive_challenges",
            )

    def test_body_tampering_breaks_signature_contract(self):
        path = "/federation/v1/gamification/challenges/c1/answer"
        headers = signed_headers(
            "friend", "secret", method="POST", path=path,
            body=b'{"answer":"A"}', action="submit_answer",
        )
        with self.assertRaises(ValueError):
            parse_request_proof(
                headers, method="POST", path=path,
                body=b'{"answer":"B"}',
            )

    def test_path_is_bound_into_signature(self):
        now = int(time.time())
        proof = GameRequestProof(
            "friend", now, "nonce_nonce_nonce_123", "GET", "/a",
            body_digest(b""), "fetch_challenge",
        )
        signature = sign_request(proof, "secret")
        changed = GameRequestProof(
            proof.peer_id, proof.timestamp, proof.nonce, proof.method, "/b",
            proof.body_sha256, proof.action,
        )
        self.assertNotEqual(signature, sign_request(changed, "secret"))

    def test_duplicate_peer_token_is_rejected(self):
        peers = [peer("a"), peer("b")]
        store = FakeFederationStore(peers, {"a": "same", "b": "same"})
        path = "/federation/v1/gamification/sessions/s1/next"
        headers = signed_headers("a", "same", method="GET", path=path, action="fetch_challenge")
        with self.assertRaises(ValueError):
            authenticate_peer(
                store, headers, method="GET", path=path, body=b"",
                required_permission="receive_challenges",
            )

    def test_provider_allowlist_is_enforced(self):
        item = peer(providers=["images"])
        self.assertTrue(peer_allows(item, "receive_challenges", provider="images"))
        self.assertFalse(peer_allows(item, "receive_challenges", provider="contacts"))

    def test_minimal_envelope_drops_internal_object_reference(self):
        envelope = minimal_challenge_envelope({
            "id": "challenge-1",
            "provider": "images",
            "kind": "tags",
            "prompt": "Was ist zu sehen?",
            "answer_type": "tags",
            "object_ref": "document:secret-id",
            "payload": {"preview": True, "path": "/secret/path.jpg", "exif": {"gps": "secret"}},
        })
        self.assertEqual(envelope["payload"], {"preview": True})
        self.assertNotIn("object_ref", envelope)
        self.assertNotIn("secret", str(envelope))


if __name__ == "__main__":
    unittest.main()
