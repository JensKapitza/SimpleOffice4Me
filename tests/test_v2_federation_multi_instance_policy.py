import tempfile
import time
import unittest
from pathlib import Path

from app.federation_trust_store import FederationTrustStore
from app.v2.authorization import AuthorizationStore, GrantRight
from app.v2.federation_policy import FederationPolicyStore


class FederationMultiInstancePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root_a = base / "peer-a"
        self.root_b = base / "peer-b"
        self.root_c = base / "peer-c"
        self.policy_a = FederationPolicyStore(self.root_a)
        self.policy_b = FederationPolicyStore(self.root_b)
        self.policy_c = FederationPolicyStore(self.root_c)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_local_block_cannot_be_overridden_by_b_or_c_state(self):
        self.policy_a.block("peer-b", scope="all", reason="local deny")
        self.policy_b.set_route_constraint(
            "peer-a",
            scope="relay",
            allowed_relays=("peer-c",),
        )
        self.policy_c.set_route_constraint(
            "peer-b",
            scope="relay",
            allowed_relays=("peer-a",),
        )

        direct = self.policy_a.decision(
            ["peer-a", "peer-b"],
            scope="relay",
            target_peer="peer-b",
        )
        indirect = self.policy_a.decision(
            ["peer-a", "peer-c", "peer-b"],
            scope="relay",
            target_peer="peer-b",
        )

        self.assertFalse(direct.allowed)
        self.assertEqual("explicit_block", direct.reason)
        self.assertFalse(indirect.allowed)
        self.assertEqual("explicit_block", indirect.reason)

    def test_positive_remote_recommendation_never_overrides_a_block(self):
        self.policy_a.block("peer-b", scope="relay")
        trust_a = FederationTrustStore(self.root_a)
        trust_a.remember("peer-b", country="DE", source="qr")
        trust_a.remember("peer-c", country="DE", source="lan")
        trust_a.set_trust(
            "peer-b",
            "HIGH",
            "VERIFIED_ADMIN",
            "TRANSITIVE",
            2,
            source_peer="peer-c",
            metadata={"imported": True},
        )

        decision = self.policy_a.decision(
            ["peer-a", "peer-b"],
            scope="relay",
            target_peer="peer-b",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual("explicit_block", decision.reason)
        self.assertEqual("peer-b", decision.blocked_peer)

    def test_rediscovery_or_qr_like_import_does_not_reactivate_blocked_peer(self):
        self.policy_a.block("peer-b", scope="all")
        trust = FederationTrustStore(self.root_a)

        trust.remember(
            "peer-b",
            country="DE",
            fingerprint="new-fingerprint",
            source="qr",
            public_key="new-public-key",
        )
        trust.set_trust(
            "peer-b",
            "HIGH",
            "VERIFIED_IN_PERSON",
            "DIRECT_ONLY",
            0,
        )

        blocks = self.policy_a.active_blocks()
        decision = self.policy_a.decision(
            ["peer-a", "peer-b"],
            scope="documents",
            target_peer="peer-b",
        )

        self.assertEqual(1, len(blocks))
        self.assertEqual("peer-b", blocks[0]["peer_id"])
        self.assertFalse(decision.allowed)
        self.assertEqual("explicit_block", decision.reason)

    def test_known_c_to_b_relay_or_delegation_blocks_a_to_c(self):
        self.policy_a.block("peer-b", scope="relay")
        auth_a = AuthorizationStore(self.root_a)
        now = int(time.time())
        auth_a.issue_root(
            issuer="peer-c",
            subject="peer-b",
            rights=(GrantRight.RELAY, GrantRight.DELEGATE),
            object_refs=("object-1",),
            expires_at=now + 600,
        )

        denied = self.policy_a.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
            authorization_store=auth_a,
            object_refs=("object-1",),
        )
        unrelated = self.policy_a.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
            authorization_store=auth_a,
            object_refs=("object-2",),
        )

        self.assertFalse(denied.allowed)
        self.assertEqual("blocked_downstream_capability", denied.reason)
        self.assertEqual("peer-b", denied.blocked_peer)
        self.assertTrue(unrelated.allowed)

    def test_security_scopes_are_enforced_independently(self):
        scopes = (
            "relay",
            "storage",
            "metadata",
            "delegation",
            "keys/capabilities",
        )
        for index, scope in enumerate(scopes):
            peer = f"peer-blocked-{index}"
            with self.subTest(scope=scope):
                self.policy_a.block(peer, scope=scope)
                denied = self.policy_a.decision(
                    ["peer-a", peer],
                    scope=scope,
                    target_peer=peer,
                )
                allowed_other = self.policy_a.decision(
                    ["peer-a", peer],
                    scope="documents",
                    target_peer=peer,
                )
                self.assertFalse(denied.allowed)
                self.assertEqual("explicit_block", denied.reason)
                self.assertTrue(allowed_other.allowed)


if __name__ == "__main__":
    unittest.main()
