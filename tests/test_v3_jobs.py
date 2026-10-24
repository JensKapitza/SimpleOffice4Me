from __future__ import annotations
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.v3_jobs import JobStore, JobWorker, RetryableJobError, default_handlers


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

    def test_progress_requires_active_worker_lease_and_registers_batch_handler(self):
        job = self.store.enqueue("documents.smart_folder_batch", {}, "alice", idempotency_key="progress")
        claimed = self.store.claim("worker-progress")
        self.assertEqual(job.job_id, claimed.job_id)

        updated = self.store.update_progress(
            job.job_id,
            "worker-progress",
            {"completed": 2, "total": 5, "action": "preview"},
        )

        self.assertEqual(2, updated.progress["completed"])
        self.assertEqual(5, updated.progress["total"])
        with self.assertRaisesRegex(ValueError, "active worker lease"):
            self.store.update_progress(job.job_id, "other-worker", {"completed": 3, "total": 5})
        handlers = default_handlers(self.root, self.store, "worker-progress")
        self.assertIn("documents.smart_folder_batch", handlers)

    def test_batch_queue_limits_active_and_recent_jobs(self):
        options = {
            "max_open_per_principal": 1,
            "max_open_total": 1,
            "max_created_per_principal_window": 1,
            "max_created_total_window": 1,
        }
        first = self.store.enqueue(
            "documents.smart_folder_batch", {}, "alice",
            idempotency_key="batch-1", **options,
        )
        replay = self.store.enqueue(
            "documents.smart_folder_batch", {}, "alice",
            idempotency_key="batch-1", **options,
        )
        self.assertEqual(first.job_id, replay.job_id)
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.store.enqueue(
                "documents.smart_folder_batch", {}, "alice",
                idempotency_key="batch-2", **options,
            )
        self.store.succeed(first.job_id)
        with self.assertRaisesRegex(ValueError, "rate limit"):
            self.store.enqueue(
                "documents.smart_folder_batch", {}, "alice",
                idempotency_key="batch-3", **options,
            )

    def test_progress_column_migrates_existing_job_database(self):
        root = self.root / "legacy"
        database = root / ".simpleoffice-meta" / "v3-jobs.sqlite3"
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as db:
            db.execute(
                """CREATE TABLE v3_job(
                    job_id TEXT PRIMARY KEY,kind TEXT NOT NULL,state TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,principal TEXT NOT NULL,
                    payload_json TEXT NOT NULL,priority INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at INTEGER NOT NULL,lease_owner TEXT NOT NULL DEFAULT '',
                    lease_until INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,
                    started_at INTEGER NOT NULL DEFAULT 0,finished_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,last_error TEXT NOT NULL DEFAULT '')"""
            )
            db.execute(
                """INSERT INTO v3_job(job_id,kind,state,idempotency_key,principal,payload_json,
                   available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("legacy-job", "test", "queued", "legacy-key", "alice", "{}", 0, 1, 1),
            )

        migrated = JobStore(root).get("legacy-job")

        self.assertIsNotNone(migrated)
        self.assertEqual({}, migrated.progress)


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
        with patch("app.v3_jobs.time.time", return_value=104):
            self.assertIsNone(replacement.run_once())
        with patch("app.v3_jobs.time.time", return_value=106):
            recovered=replacement.run_once()
        self.assertEqual("succeeded",recovered.state)
        self.assertEqual(2,recovered.attempt)
        self.assertEqual([(job.job_id,{"payload":"kept"})],seen)


if __name__=="__main__":
    unittest.main()
