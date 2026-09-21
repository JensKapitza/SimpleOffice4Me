import tempfile
import time
import unittest

from app.v2.authorization import AuthorizationStore, GrantRight
from app.v2.jobs import FederationJobService, FederationTransferIntent, PersistentJobStore


class AuthorizedFederationJobTests(unittest.TestCase):
    def test_transfer_requires_relay_right_for_every_object(self):
        with tempfile.TemporaryDirectory() as root:
            auth = AuthorizationStore(root)
            grant = auth.issue_root(
                issuer="peer-a",
                subject="peer-b",
                rights=[GrantRight.RELAY],
                object_refs=["object-1"],
                expires_at=int(time.time()) + 600,
            )
            service = FederationJobService(PersistentJobStore(root))
            intent = FederationTransferIntent(
                source_peer="peer-b",
                target_peer="peer-c",
                object_refs=("object-1", "object-2"),
                authorization_ref=grant.grant_id,
                expires_at=int(time.time()) + 300,
            )
            result = service.create_transfer(intent, idempotency_key="denied", authorization_store=auth)
            self.assertFalse(result.ok)
            self.assertEqual("forbidden", result.error.code.value)

    def test_revoked_capability_cannot_create_transfer(self):
        with tempfile.TemporaryDirectory() as root:
            auth = AuthorizationStore(root)
            grant = auth.issue_root(
                issuer="peer-a",
                subject="peer-b",
                rights=[GrantRight.RELAY],
                object_refs=["object-1"],
                expires_at=int(time.time()) + 600,
            )
            auth.revoke(grant.grant_id)
            service = FederationJobService(PersistentJobStore(root))
            intent = FederationTransferIntent(
                source_peer="peer-b",
                target_peer="peer-c",
                object_refs=("object-1",),
                authorization_ref=grant.grant_id,
                expires_at=int(time.time()) + 300,
            )
            result = service.create_transfer(intent, idempotency_key="revoked", authorization_store=auth)
            self.assertFalse(result.ok)

    def test_valid_relay_capability_creates_persistent_job(self):
        with tempfile.TemporaryDirectory() as root:
            auth = AuthorizationStore(root)
            grant = auth.issue_root(
                issuer="peer-a",
                subject="peer-b",
                rights=[GrantRight.RELAY],
                object_refs=["object-1"],
                expires_at=int(time.time()) + 600,
            )
            service = FederationJobService(PersistentJobStore(root))
            intent = FederationTransferIntent(
                source_peer="peer-b",
                target_peer="peer-c",
                object_refs=("object-1",),
                authorization_ref=grant.grant_id,
                expires_at=int(time.time()) + 300,
            )
            result = service.create_transfer(intent, idempotency_key="allowed", authorization_store=auth)
            self.assertTrue(result.ok)
            self.assertEqual(grant.grant_id, result.value.payload["authorization_ref"])


if __name__ == "__main__":
    unittest.main()
