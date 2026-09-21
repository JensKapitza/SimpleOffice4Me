import tempfile
import time
import unittest

from app.v2.authorization import AuthorizationStore, GrantRight
from app.v2.jobs import FederationJobService, FederationTransferIntent, PersistentJobStore


class FederationAuthorizationExpiryTests(unittest.TestCase):
    def test_job_cannot_outlive_its_relay_capability(self):
        with tempfile.TemporaryDirectory() as root:
            now = int(time.time())
            auth = AuthorizationStore(root)
            grant = auth.issue_root(
                issuer="peer-a",
                subject="peer-b",
                rights=[GrantRight.RELAY],
                object_refs=["object-1"],
                expires_at=now + 120,
            )
            service = FederationJobService(PersistentJobStore(root))
            intent = FederationTransferIntent(
                source_peer="peer-b",
                target_peer="peer-c",
                object_refs=("object-1",),
                authorization_ref=grant.grant_id,
                expires_at=now + 300,
            )
            result = service.create_transfer(
                intent,
                idempotency_key="outlives-capability",
                authorization_store=auth,
            )
            self.assertFalse(result.ok)
            self.assertEqual("forbidden", result.error.code.value)

    def test_job_within_capability_lifetime_is_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            now = int(time.time())
            auth = AuthorizationStore(root)
            grant = auth.issue_root(
                issuer="peer-a",
                subject="peer-b",
                rights=[GrantRight.RELAY],
                object_refs=["object-1"],
                expires_at=now + 600,
            )
            service = FederationJobService(PersistentJobStore(root))
            intent = FederationTransferIntent(
                source_peer="peer-b",
                target_peer="peer-c",
                object_refs=("object-1",),
                authorization_ref=grant.grant_id,
                expires_at=now + 300,
            )
            result = service.create_transfer(
                intent,
                idempotency_key="within-capability",
                authorization_store=auth,
            )
            self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
