"""Exercise delayed copy notices through the real copy route and persisted worker."""
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import get_db
from app.document_store import DocumentStore
from app.settings_store import SettingsStore
from app.v3_automation import AutomationStore
from app.v3_automation_timing import utc_instant
from app.v3_copy_notices import NOTICE_JOB_KIND
from app.v3_jobs import JobStore, JobWorker, default_handlers
from tests.http_api_fixture import PublicHttpTestCase


class DelayedCopyNoticeTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        store = DocumentStore(self.root)
        self.original = store.import_upload(BytesIO(b"synthetic"), "sample.txt", "owner")
        (self.root / "copies").mkdir()
        self.settings = SettingsStore(self.root)
        self.settings.save({"v3": {"v3.activity": True, "v3.automation": True, "v3.jobs": True}}, "owner")
        self.csrf = self._csrf_headers(self.client)

    def _queue(self):
        response = self.client.post("/automation-v3/copy-notice", data={"delay": "30"}, headers=self.csrf)
        self.assertEqual(302, response.status_code)
        copy = self.client.post(
            f"/documents/{self.original['document_id']}/copy",
            data={"destination_folder": "copies"}, headers=self.csrf,
        )
        self.assertEqual(302, copy.status_code)
        self.assertEqual(b"synthetic", (self.root / "copies" / "sample.txt").read_bytes())
        job, = JobStore(self.root).list(kind=NOTICE_JOB_KIND)
        self.assertEqual("queued", job.state)
        return job

    def _advance(self, at, worker_id="fresh-worker"):
        queue = JobStore(self.root)
        worker = JobWorker(queue, default_handlers(self.root, queue, worker_id), worker_id)
        with (patch("app.v3_jobs.time.time", return_value=at.timestamp()),
              patch("app.v3_copy_notices._now", return_value=at)):
            return worker.run_once()

    def _feed(self):
        return self.client.get("/automation-v3/copy-notices").get_json()["notices"]

    def test_notice_appears_once_after_worker_restart_and_not_before_due(self):
        job = self._queue()
        due = utc_instant(job.payload["window"]["not_before"])
        self.assertIsNone(self._advance(due - timedelta(seconds=2)))
        self.assertEqual([], self._feed())
        completed = self._advance(due + timedelta(seconds=2))
        self.assertEqual("succeeded", completed.state)
        notice, = self._feed()
        self.assertEqual("browser", notice["kind"])
        self.assertIsNone(self._advance(due + timedelta(seconds=8), "another-worker"))
        self.assertEqual([notice], self._feed())

    def test_overdue_job_records_missed_window_and_does_not_emit_notice(self):
        job = self._queue()
        expires = utc_instant(job.payload["window"]["expires_at"])
        self.assertEqual("succeeded", self._advance(expires + timedelta(seconds=2)).state)
        self.assertEqual([], self._feed())
        self.assertIn("window_missed", [item["status"] for item in AutomationStore(self.root).logs("owner")])

    def test_rule_disabled_after_enqueue_cannot_deliver(self):
        job = self._queue()
        rule_id = job.payload["rule_id"]
        self.client.post(f"/automation-v3/{rule_id}/toggle", headers=self.csrf)
        due = utc_instant(job.payload["window"]["not_before"])
        self.assertEqual("failed", self._advance(due + timedelta(seconds=2)).state)
        self.assertEqual([], self._feed())

    def test_account_version_changed_after_enqueue_cannot_deliver(self):
        job = self._queue()
        with app.app_context():
            db = get_db()
            db.execute("UPDATE user SET auth_version=auth_version+1 WHERE username=?", ("owner",))
            db.commit()
        due = utc_instant(job.payload["window"]["not_before"])
        self.assertEqual("failed", self._advance(due + timedelta(seconds=2)).state)
        with app.app_context():
            db = get_db()
            db.execute("UPDATE user SET auth_version=auth_version-1 WHERE username=?", ("owner",))
            db.commit()
        self.assertEqual([], self._feed())

    def test_timer_requires_jobs_enabled_and_accepts_only_supported_values(self):
        route = "/automation-v3/copy-notice"
        self.assertEqual(400, self.client.post(route, data={"delay": "31"}, headers=self.csrf).status_code)
        self.settings.save({"v3": {"v3.activity": True, "v3.automation": True, "v3.jobs": False}}, "owner")
        self.assertEqual(409, self.client.post(route, data={"delay": "30"}, headers=self.csrf).status_code)
        self.assertEqual([], AutomationStore(self.root).list("owner"))
