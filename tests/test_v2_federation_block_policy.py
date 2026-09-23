import tempfile
import time
import unittest

from app.v2.federation_policy import FederationPolicyStore
from app.v2.authorization import AuthorizationStore, GrantRight
from app.v2.contracts import JobState
from app.v2.jobs import FederationJobService, FederationTransferIntent, PersistentJobStore


class FederationBlockPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.policy = FederationPolicyStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_direct_block_denies_route(self):
        self.policy.block("peer-b")
        decision = self.policy.decision(["peer-a", "peer-b"], scope="documents")
        self.assertFalse(decision.allowed)
        self.assertEqual("peer-b", decision.blocked_peer)

    def test_block_denies_indirect_route(self):
        self.policy.block("peer-b")
        decision = self.policy.decision(["peer-a", "peer-c", "peer-b"], scope="relay")
        self.assertFalse(decision.allowed)

    def test_scope_block_does_not_expand_to_other_scope(self):
        self.policy.block("peer-b", scope="chat")
        self.assertFalse(self.policy.decision(["peer-b"], scope="chat").allowed)
        self.assertTrue(self.policy.decision(["peer-b"], scope="documents").allowed)

    def test_global_block_overrides_specific_scope(self):
        self.policy.block("peer-b", scope="all")
        self.assertFalse(self.policy.decision(["peer-b"], scope="storage").allowed)

    def test_expired_block_is_ignored(self):
        now = int(time.time())
        self.policy.block("peer-b", expires_at=now + 10)
        self.assertTrue(self.policy.decision(["peer-b"], scope="relay", now=now + 11).allowed)

    def test_unknown_route_fails_closed(self):
        decision = self.policy.decision([], scope="relay")
        self.assertFalse(decision.allowed)
        self.assertEqual("route_unknown", decision.reason)

    def test_job_creation_checks_complete_known_route(self):
        self.policy.block("peer-b")
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        result = service.create_transfer(
            intent,
            idempotency_key="blocked-route",
            policy_store=self.policy,
            route=("peer-a", "peer-c", "peer-b"),
        )
        self.assertFalse(result.ok)

    def test_later_block_stops_waiting_job_before_progress(self):
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a", target_peer="peer-c",
            object_refs=("object-1",), authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        created = service.create_transfer(
            intent, idempotency_key="later-block",
            policy_store=self.policy, route=("peer-a", "peer-c"),
        )
        self.assertTrue(created.ok)
        self.policy.block("peer-c")
        checked = service.enforce_policy(created.value.job_id, policy_store=self.policy)
        self.assertEqual(JobState.FAILED, checked.value.state)
        self.assertTrue(checked.value.payload["policy_denied"])

    def test_block_revokes_capability_for_blocked_peer(self):
        auth = AuthorizationStore(self.root)
        grant = auth.issue_root(
            issuer="peer-a", subject="peer-c",
            rights=(GrantRight.RELAY,), object_refs=("object-1",),
            expires_at=int(time.time()) + 600,
        )
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a", target_peer="peer-c",
            object_refs=("object-1",), authorization_ref=grant.grant_id,
            expires_at=int(time.time()) + 300,
        )
        created = service.create_transfer(
            intent, idempotency_key="revoke-block",
            policy_store=self.policy, route=("peer-a", "peer-c"),
        )
        self.policy.block("peer-c")
        service.enforce_policy(
            created.value.job_id, policy_store=self.policy, authorization_store=auth
        )
        self.assertTrue(auth.get(grant.grant_id).revoked)

    def test_stop_blocked_jobs_rechecks_existing_queue(self):
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a", target_peer="peer-c",
            object_refs=("object-1",), authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        created = service.create_transfer(
            intent, idempotency_key="queue-recheck",
            policy_store=self.policy, route=("peer-a", "peer-c"),
        )
        self.policy.block("peer-c")
        self.assertEqual(1, service.stop_blocked_jobs(policy_store=self.policy))
        self.assertEqual(JobState.FAILED, service.store.get(created.value.job_id).value.state)


if __name__ == "__main__":
    unittest.main()
