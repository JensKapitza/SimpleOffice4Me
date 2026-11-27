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
