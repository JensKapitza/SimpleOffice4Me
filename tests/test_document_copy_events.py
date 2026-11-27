from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app import app
from app.document_copy_events import EVENT_NAME, capture_copy_completed
from app.document_store import DocumentStore
from app.settings_store import SettingsStore
from app.v3_activity import ActivityStore
from tests.http_api_fixture import PublicHttpTestCase


class DocumentCopyEventTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        self.store = DocumentStore(self.root)
        self.document = self.store.import_upload(BytesIO(b"example bytes"), "sensitive-name.txt", "owner")
        self.url = f"/documents/{self.document['document_id']}/copy"
        (self.root / "copies").mkdir()
        self.csrf = self._csrf_headers(self.client)

    def _enable_activity(self):
        SettingsStore(self.root).save({"v3": {"v3.activity": True}}, "owner")

    def _copy(self):
        return self.client.post(self.url, data={"destination_folder": "copies"}, headers=self.csrf)

    def test_verified_copy_records_one_private_receipt_and_collision_adds_none(self):
        self._enable_activity()
        result = self._copy()
        self.assertEqual(result.status_code, 302)
        self.assertNotIn(self.document["document_id"], result.headers["Location"])
        copy_id = result.headers["Location"].rsplit("/", 1)[1]
        copied = self.root / "copies" / "sensitive-name.txt"
        self.assertEqual(copied.read_bytes(), b"example bytes")

        first, = ActivityStore(self.root).list(event_type=EVENT_NAME, actor="owner")
        self.assertEqual(first.entity_id, copy_id)
        self.assertEqual(first.metadata, {})
        self.assertEqual(first.source, "browser.document_copy")
        self.assertEqual(first.event_id, capture_copy_completed(self.root, "owner", copy_id))

        second = self._copy()
        self.assertEqual(second.status_code, 302)
        self.assertIn(self.document["document_id"], second.headers["Location"])
        rows = ActivityStore(self.root).list(event_type=EVENT_NAME, actor="owner")
        self.assertEqual([row.event_id for row in rows], [first.event_id])

    def test_unapproved_browser_post_cannot_emit_copy_event(self):
        self._enable_activity()
        result = self.client.post(self.url, data={"destination_folder": "copies"})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(ActivityStore(self.root).list(event_type=EVENT_NAME), [])
        self.assertEqual(list((self.root / "copies").iterdir()), [])

    def test_copy_works_without_activity_capability(self):
        result = self._copy()
        self.assertEqual(result.status_code, 302)
        self.assertNotIn(self.document["document_id"], result.headers["Location"])
        self.assertFalse((self.root / ".simpleoffice-meta" / "v3-activity.sqlite3").exists())

    def test_optional_activity_failure_does_not_change_successful_copy(self):
        self._enable_activity()
        with patch("app.document_copy_events.ActivityStore", side_effect=OSError("private detail")):
            with self.assertLogs("app.document_copy_events", level="WARNING") as captured:
                result = self._copy()
        self.assertEqual(result.status_code, 302)
        self.assertNotIn(self.document["document_id"], result.headers["Location"])
        self.assertEqual((self.root / "copies" / "sensitive-name.txt").read_bytes(), b"example bytes")
        self.assertNotIn("private detail", " ".join(captured.output))

    def test_activate_copy_notice_then_copy_and_read_it_as_owner_only(self):
        from app.v3_automation import AutomationStore
        from app.v3_copy_notices import NOTICE

        SettingsStore(self.root).save({"v3": {"v3.activity": True, "v3.automation": True}}, "owner")
        path = "/automation-v3/copy-notice"
        self.assertEqual(self.client.post(path).status_code, 403)
        for _ in range(2):
            self.assertEqual(self.client.post(path, headers=self.csrf).status_code, 302)
        rule, = AutomationStore(self.root).list("owner")
        copied = self._copy()
        copy_id = copied.headers["Location"].rsplit("/", 1)[1]
        self.assertEqual(self._copy().status_code, 302)  # target collision, no second notice
        capture_copy_completed(self.root, "owner", copy_id)
        notice, = ActivityStore(self.root).list(event_type=NOTICE)
        self.assertEqual(notice.actor, "owner")
        self.assertEqual(notice.entity_id, "")
        self.assertEqual(notice.metadata, {})
        page = self.client.get("/automation-v3").get_data(as_text=True)
        self.assertIn(notice.occurred_at, page)
        notice_card = page.split('aria-labelledby="copy-notices-heading"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn("sensitive-name.txt", notice_card)

        other = app.test_client()
        self._register(self.client, "other")
        self._login(other, "other")
        self.assertNotIn(notice.occurred_at, other.get("/automation-v3").get_data(as_text=True))
        self.assertIsNone(ActivityStore(self.root).find_for_actor(notice.event_id, "other"))

        self.assertEqual(self.client.post(f"/automation-v3/{rule.rule_id}/toggle", headers=self.csrf).status_code, 302)
        (self.root / "after-disabled").mkdir()
        result = self.client.post(self.url, data={"destination_folder": "after-disabled"}, headers=self.csrf)
        self.assertEqual(result.status_code, 302)
        self.assertTrue((self.root / "after-disabled" / "sensitive-name.txt").is_file())
        self.assertEqual(ActivityStore(self.root).list(event_type=NOTICE), [notice])

    def test_receipt_retry_keeps_original_timestamp(self):
        from app.v3_copy_notices import dispatch_copy_notice

        self._enable_activity()
        with patch("app.v3_copy_notices.dispatch_copy_notice", wraps=dispatch_copy_notice) as dispatch:
            capture_copy_completed(self.root, "owner", "copy-fixed-id")
            capture_copy_completed(self.root, "owner", "copy-fixed-id")
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(dispatch.call_args_list[0].args[1], dispatch.call_args_list[1].args[1])

    def test_browser_copy_does_not_dispatch_mixed_or_different_owner_actions(self):
        from app.v3_automation import AutomationStore
        from app.v3_copy_notices import ACTION, NOTICE

        SettingsStore(self.root).save({"v3": {"v3.activity": True, "v3.automation": True}}, "owner")
        store = AutomationStore(self.root)
        values = {"name": "Notice", "enabled": True, "trigger": {"type": "event", "name": EVENT_NAME},
                  "actions": [{"type": ACTION}]}
        store.create("other", values)
        store.create("owner", {**values, "actions": [{"type": ACTION}, {"type": "task.create"}]})
        store.create("owner", {**values, "actions": [{"type": ACTION, "payload": {"target": "peer"}}]})
        self.assertEqual(self._copy().status_code, 302)
        self.assertEqual(ActivityStore(self.root).list(event_type=NOTICE), [])
        self.assertEqual(store.logs("owner"), [])


    def test_feed_is_private_minimal_and_independent_of_browser_refresh(self):
        SettingsStore(self.root).save({"v3": {"v3.activity": True, "v3.automation": True}}, "owner")
        endpoint = "/automation-v3/copy-notices"
        self.assertEqual(app.test_client().get(endpoint).status_code, 302)
        self.assertIn(b'href="/automation-v3"', self.client.get("/tasks/").data)
        initial = self.client.get(endpoint)
        self.assertEqual({"notices": []}, initial.get_json())
        self.assertIn("no-store", initial.headers["Cache-Control"])
        self.assertIn("Cookie", initial.headers["Vary"])
        self.client.post("/automation-v3/copy-notice", headers=self.csrf)
        self.assertEqual(self._copy().status_code, 302)
        fresh = self.client.get(endpoint)
        notice, = fresh.get_json()["notices"]
        self.assertEqual({"id", "kind", "at"}, set(notice))
        self.assertEqual("browser", notice["kind"])
        self.assertNotIn(b"sensitive-name.txt", fresh.data)
        self._register(self.client, "other")
        other = app.test_client()
        self._login(other, "other")
        self.assertEqual({"notices": []}, other.get(endpoint).get_json())
        SettingsStore(self.root).save({"v3": {"v3.activity": False, "v3.automation": True}}, "owner")
        self.assertEqual(404, self.client.get(endpoint).status_code)
