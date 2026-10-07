from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from app.v3_jobs import JobStore, JobWorker, RetryableJobError


class V3JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=JobStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_enqueue_is_idempotent(self):
        first=self.store.enqueue("test",{"x":1},"alice",idempotency_key="same")
        second=self.store.enqueue("test",{"x":2},"alice",idempotency_key="same")
        self.assertEqual(first.job_id,second.job_id)
        self.assertEqual({"x":1},second.payload)
        self.assertEqual(0,first.available_at)

    def test_expired_running_lease_is_recovered(self):
        job=self.store.enqueue("test",{},"alice",idempotency_key="recover")
        claimed=self.store.claim("dead",lease_seconds=5,now=100)
        self.assertEqual(job.job_id,claimed.job_id)
        recovered=self.store.claim("new",lease_seconds=5,now=106)
        self.assertEqual(job.job_id,recovered.job_id)
        self.assertEqual(2,recovered.attempt)

    def test_retry_backoff_and_terminal_failure(self):
        self.store.enqueue("test",{},"alice",idempotency_key="retry",max_attempts=2)
        first=self.store.claim("w",now=100)
        retry=self.store.fail(first.job_id,"temporary detail",retryable=True,now=100)
        self.assertEqual("retry_scheduled",retry.state)
        self.assertIsNone(self.store.claim("w2",now=104))
        second=self.store.claim("w2",now=retry.available_at)
        failed=self.store.fail(second.job_id,"still broken",retryable=True,now=retry.available_at)
        self.assertEqual("failed",failed.state)

    def test_broken_job_does_not_block_next_job(self):
        self.store.enqueue("bad",{},"alice",idempotency_key="1",priority=10,max_attempts=1)
        self.store.enqueue("good",{},"alice",idempotency_key="2")
        seen=[]
        worker=JobWorker(self.store,{"bad":lambda job: (_ for _ in ()).throw(RuntimeError("private")),"good":lambda job: seen.append(job.job_id)},"worker")
        self.assertEqual("failed",worker.run_once().state)
        self.assertEqual("succeeded",worker.run_once().state)
        self.assertEqual(1,len(seen))

    def test_retryable_handler_is_scheduled(self):
        self.store.enqueue("retry",{},"alice",idempotency_key="3")
        worker=JobWorker(self.store,{"retry":lambda job: (_ for _ in ()).throw(RetryableJobError("later"))},"worker")
        self.assertEqual("retry_scheduled",worker.run_once().state)

    def test_metrics_are_bounded_summary(self):
        self.store.enqueue("a",{},"alice",idempotency_key="4")
        metrics=self.store.metrics()
        self.assertEqual(1,metrics["queued"])


    def test_new_worker_instance_recovers_expired_process_lease(self):
        job=self.store.enqueue("test",{"payload":"kept"},"alice",idempotency_key="process-restart")
        abandoned=self.store.claim("worker-before-crash",lease_seconds=5,now=100)
        self.assertEqual(job.job_id,abandoned.job_id)
        self.assertEqual("running",abandoned.state)

        seen=[]
        replacement=JobWorker(
            JobStore(self.root),
            {"test":lambda current: seen.append((current.job_id,current.payload))},
            "worker-after-restart",
        )
        self.assertIsNone(replacement.run_once(now=104))
        recovered=replacement.run_once(now=106)
        self.assertEqual("succeeded",recovered.state)
        self.assertEqual(2,recovered.attempt)
        self.assertEqual([(job.job_id,{"payload":"kept"})],seen)


if __name__=="__main__":
    unittest.main()
