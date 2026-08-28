import json
import tempfile
import unittest
from pathlib import Path

from app.document_store import CONTROL_DIR
from app.todo_store import TodoConflict, TodoStore


class TodoStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir()
        self.store = TodoStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_task_is_preserved_and_claimed_on_first_update(self):
        path = self.root / CONTROL_DIR / "todo.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"items": [{"id": "legacy", "title": "Altbestand", "done": False}]}), encoding="utf-8")
        self.assertEqual("Altbestand", self.store.items("admin")[0]["title"])
        changed = self.store.toggle("legacy", "admin")
        self.assertTrue(changed["done"])
        self.assertEqual("admin", json.loads(path.read_text(encoding="utf-8"))["items"][0]["owner"])

    def test_users_are_isolated_and_fields_roundtrip(self):
        task = self.store.add("Angebot", "admin", {"description": "Prüfen", "due": "2026-09-01", "priority": "1", "categories": "CRM, Büro"})
        self.store.add("Privat", "other")
        self.assertEqual(["Angebot"], [item["title"] for item in self.store.items("admin")])
        self.assertEqual(["Büro", "CRM"], task["categories"])
        updated = self.store.update(task["id"], {"percent_complete": "50", "status": "in-process"}, "admin")
        self.assertEqual(50, updated["percent_complete"])
        self.assertEqual("in-process", updated["status"])

    def test_resource_etag_sync_and_tombstone(self):
        values = {"uid": "task-1@example.test", "title": "CalDAV", "status": "needs-action", "due": "2026-09-02"}
        item, created = self.store.put_resource("task.ics", values, "admin", create_only=True)
        self.assertTrue(created)
        etag = self.store.etag(item)
        with self.assertRaises(TodoConflict):
            self.store.put_resource("task.ics", {**values, "title": "Verloren"}, "admin", '"stale"')
        changed, token = self.store.sync_changes("admin", "urn:simpleoffice:caldav:tasks:admin:0")
        self.assertEqual("task.ics", changed[0]["resource"])
        self.store.delete_resource("task.ics", "admin", etag)
        deleted, new_token = self.store.sync_changes("admin", token)
        self.assertTrue(deleted[0]["deleted"])
        self.assertTrue(new_token.endswith(":2"))


if __name__ == "__main__":
    unittest.main()
