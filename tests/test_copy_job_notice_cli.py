"""Run the actual Flask command and read notices through the authenticated UI."""
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import get_db
from app.document_copy_events import capture_copy_job_completed
from app.replication_store import ReplicationStore
from app.settings_store import SettingsStore
from app.v3_activity import ActivityStore
from app.v3_copy_notices import JOB_EVENT_NAME, JOB_NOTICE

from tests.http_api_fixture import PublicHttpTestCase


class CopyJobNoticeCliTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        self.root.mkdir(exist_ok=True)
        self.replications = ReplicationStore(self.root)
        self.replications.control.mkdir(exist_ok=True)
        (self.replications.control / "notes.json").write_text("synthetic file", encoding="utf-8")
        self.dest = self.root.parent / "copy-target"
        self.dest.mkdir()
        target = self.replications.add_target({"label": "Test copy", "path": str(self.dest)}, "owner")
        self.replications.add_rule({"label": "Notes", "target_id": target["target_id"], "categories": ["notes"]}, "owner")
        self.output = self.dest / "SimpleOffice-Spiegelung/control/notes.json"
        SettingsStore(self.root).save({"v3": {"v3.activity": True, "v3.automation": True}}, "owner")
        self.activity = ActivityStore(self.root)
        self.csrf = self._csrf_headers(self.client)

    def _enable_job_notice(self):
        path = "/automation-v3/copy-notice"
        self.assertEqual(self.client.post(path, data={"source": "job"}).status_code, 403)
        self.assertEqual(self.client.post(path, data={"source": "outbound"}, headers=self.csrf).status_code, 400)
        self.assertEqual(self.client.post(path, data={"source": "job"}, headers=self.csrf).status_code, 302)

    def _run(self, *extra):
        result = app.test_cli_runner().invoke(args=["run-replications", "--run-id", "timer-1", *extra])
        # Command runs outside an explicitly entered app context, like the actual cron entry point.
        return result

    def _notices(self):
        return self.activity.list(event_type=JOB_NOTICE)

    def test_scheduled_job_routes_to_opted_in_local_account_not_audit_actor(self):
        self._enable_job_notice()
        self._register(self.client, "other")
        other = app.test_client()
        self._login(other, "other")
        result = self._run("--notify-account", "owner")
        self.assertEqual(result.exit_code, 0, str(result.exception))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "synthetic file")
        event, = self.activity.list(event_type=JOB_EVENT_NAME)
        self.assertEqual(event.actor, "system")
        notice, = self._notices()
        self.assertEqual(notice.actor, "owner")
        self.assertEqual(notice.correlation_id, event.event_id)
        self.assertEqual(notice.metadata, {})
        owner_page = self.client.get("/automation-v3").get_data(as_text=True)
        other_page = other.get("/automation-v3").get_data(as_text=True)
        self.assertIn(notice.occurred_at, owner_page)
        self.assertNotIn(notice.occurred_at, other_page)
        feed = "/automation-v3/copy-notices"
        self.assertEqual([{"id": notice.event_id, "kind": "job", "at": notice.occurred_at}],
                         self.client.get(feed).get_json()["notices"])
        self.assertEqual([], other.get(feed).get_json()["notices"])
        result = self._run("--notify-account", "owner")
        self.assertEqual(result.exit_code, 0, str(result.exception))  # unchanged files
        self.output.unlink()
        result = self._run("--notify-account", "owner")
        self.assertEqual(result.exit_code, 0, str(result.exception))  # same stable run ID
        self.assertEqual(self._notices(), [notice])

    def test_missing_recipient_option_cannot_route_via_actor(self):
        self._enable_job_notice()
        result = self._run("--actor", "owner")
        self.assertEqual(result.exit_code, 0, str(result.exception))
        self.assertTrue(self.output.is_file())
        self.assertEqual(self._notices(), [])

    def test_named_account_without_opt_in_never_gets_notice(self):
        result = self._run("--notify-account", "owner")
        self.assertEqual(result.exit_code, 0, str(result.exception))
        self.assertTrue(self.output.is_file())
        self.assertEqual(self._notices(), [])

    def test_invalid_case_or_disabled_recipient_rejected_before_copy(self):
        for name in ("unknown", "OWNER"):
            result = self._run("--notify-account", name)
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("active local account", result.output)
            self.assertFalse(self.output.exists())
        with app.app_context():
            db = get_db()
            db.execute("UPDATE user SET is_disabled=1 WHERE username=?", ("owner",))
            db.commit()
        result = self._run("--notify-account", "owner")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("active local account", result.output)
        self.assertFalse(self.output.exists())

    def test_partially_completed_batch_never_sends_notice(self):
        self._enable_job_notice()
        second = self.root.parent / "secondary-target"
        second.mkdir()
        target = self.replications.add_target({"label": "Missing", "path": str(second)}, "owner")
        self.replications.add_rule({"label": "Failing copy", "target_id": target["target_id"], "categories": ["notes"]}, "owner")
        second.rmdir()
        result = self._run("--notify-account", "owner")
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn("one or more replications failed", result.output)
        self.assertTrue(self.output.is_file())
        self.assertEqual(self._notices(), [])

    def test_account_revoked_during_job_leaves_successful_copy_but_no_notice(self):
        self._enable_job_notice()

        def capture_and_revoke(*args, **kwargs):
            event_id = capture_copy_job_completed(*args, **kwargs)
            db = get_db()
            db.execute("UPDATE user SET is_disabled=1 WHERE username=?", ("owner",))
            db.commit()
            return event_id

        with patch("app.replication_store.capture_copy_job_completed", side_effect=capture_and_revoke):
            result = self._run("--notify-account", "owner")
        self.assertEqual(result.exit_code, 0, str(result.exception))
        self.assertTrue(self.output.is_file())
        self.assertEqual(self._notices(), [])
