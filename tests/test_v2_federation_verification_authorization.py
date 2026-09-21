import tempfile
import time
import unittest

from app.v2.authorization import AuthorizationStore, GrantRight
from app.v2.contracts import JobState
from app.v2.jobs import FederationJobService, FederationTransferIntent, PersistentJobStore


class FederationVerificationAuthorizationTests(unittest.TestCase):
    def _setup(self, root):
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
        job = service.create_transfer(
            intent,
            idempotency_key="verification-auth",
            authorization_store=auth,
        ).value
        return auth, grant, service, job

    def test_revoked_grant_cannot_complete_existing_job(self):
        with tempfile.TemporaryDirectory() as root:
            auth, grant, service, job = self._setup(root)
            auth.revoke(grant.grant_id)
            result = service.mark_verified(
                job.job_id,
                "object-1",
                observed_via="peer",
                authorization_store=auth,
            )
            self.assertTrue(result.ok)
            self.assertEqual(JobState.FAILED, result.value.state)
            self.assertEqual([], result.value.payload["verified_object_refs"])

    def test_effective_grant_can_complete_existing_job(self):
        with tempfile.TemporaryDirectory() as root:
            auth, _grant, service, job = self._setup(root)
            result = service.mark_verified(
                job.job_id,
                "object-1",
                observed_via="peer",
                authorization_store=auth,
            )
            self.assertTrue(result.ok)
            self.assertEqual(JobState.SUCCEEDED, result.value.state)


if __name__ == "__main__":
    unittest.main()
