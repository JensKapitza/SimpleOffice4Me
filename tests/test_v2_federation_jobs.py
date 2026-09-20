import tempfile
import time
import unittest
from pathlib import Path

from app.v2.contracts import JobState
from app.v2.jobs import FederationJobService, FederationTransferIntent, PersistentJobStore


class V2FederationJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = PersistentJobStore(self.root)
        self.service = FederationJobService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _intent(self):
        return FederationTransferIntent(
            source_peer="peer-b",
            target_peer="peer-c",
            object_refs=("object-1", "object-2"),
            authorization_ref="grant-123",
            expires_at=int(time.time()) + 3600,
        )

    def test_idempotent_creation_survives_restart(self):
        first = self.service.create_transfer(self._intent(), idempotency_key="request-1")
        second = self.service.create_transfer(self._intent(), idempotency_key="request-1")
        self.assertTrue(first.ok)
        self.assertEqual(first.value.job_id, second.value.job_id)

        reopened = PersistentJobStore(self.root)
        loaded = reopened.get(first.value.job_id)
        self.assertTrue(loaded.ok)
        self.assertEqual(JobState.QUEUED, loaded.value.state)

    def test_worker_claim_is_persistent_and_increments_attempt(self):
        created = self.service.create_transfer(self._intent(), idempotency_key="request-2").value
        claimed = self.store.claim("worker-1", lease_seconds=30)
        self.assertTrue(claimed.ok)
        self.assertEqual(created.job_id, claimed.value.job_id)
        self.assertEqual(JobState.RUNNING, claimed.value.state)
        self.assertEqual(1, claimed.value.attempt)

    def test_verified_target_state_can_be_satisfied_by_alternative_transport(self):
        created = self.service.create_transfer(self._intent(), idempotency_key="request-3").value
        one = self.service.mark_verified(created.job_id, "object-1", observed_via="local-import")
        self.assertEqual(JobState.WAITING, one.value.state)
        two = self.service.mark_verified(created.job_id, "object-2", observed_via="direct-peer-transfer")
        self.assertEqual(JobState.SUCCEEDED, two.value.state)
        self.assertEqual(
            ["object-1", "object-2"],
            two.value.payload["verified_object_refs"],
        )

    def test_transfer_scope_does_not_expand(self):
        created = self.service.create_transfer(self._intent(), idempotency_key="request-4").value
        denied = self.service.mark_verified(created.job_id, "object-outside-scope", observed_via="peer")
        self.assertFalse(denied.ok)


if __name__ == "__main__":
    unittest.main()
