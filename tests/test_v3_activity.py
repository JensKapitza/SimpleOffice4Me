from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.contact_store import ContactStore
from app.document_store import DocumentStore
from app.project_store import ProjectStore
from app.v3_activity import ActivityStore, EventBus


class ActivityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_event_ids_are_idempotent_and_queries_are_bounded(self):
        store = ActivityStore(self.root)
        first = store.record("contact.created", "alice", entity_type="contact", entity_id="c1", event_id="fixed")
        second = store.record("contact.created", "alice", entity_type="contact", entity_id="c1", event_id="fixed")
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(1, len(store.list(entity_type="contact", entity_id="c1", limit=9999)))

    def test_sensitive_metadata_is_rejected(self):
        store = ActivityStore(self.root)
        with self.assertRaises(ValueError):
            store.record("x", "alice", metadata={"access_token": "secret"})

    def test_consumer_failure_is_isolated(self):
        store = ActivityStore(self.root)
        event = store.record("x", "alice")
        bus = EventBus()
        seen = []
        bus.subscribe(lambda value: (_ for _ in ()).throw(RuntimeError("down")))
        bus.subscribe(lambda value: seen.append(value.event_id))
        errors = bus.publish(event)
        self.assertEqual(1, len(errors))
        self.assertEqual([event.event_id], seen)

    def test_existing_contact_project_and_document_modules_emit_through_adapter(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_V3_ACTIVITY_ENABLED": "1"}):
            contact = ContactStore(self.root).upsert({"display_name": "Alice"}, "alice")
            project = ProjectStore(self.root).create_project({"title": "P"}, "alice")
            source = self.root.parent / (self.root.name + "-activity-source.txt")
            try:
                source.write_text("document", encoding="utf-8")
                DocumentStore(self.root).import_file(source, "alice")
            finally:
                source.unlink(missing_ok=True)

        rows = ActivityStore(self.root).list(limit=20)
        types = {row.event_type for row in rows}
        self.assertIn("contact.created", types)
        self.assertIn("project.created", types)
        self.assertIn("document.imported", types)
        self.assertTrue(any(row.entity_id == contact["contact_id"] for row in rows))
        self.assertTrue(any(row.entity_id == project["project_id"] for row in rows))


if __name__ == "__main__":
    unittest.main()
