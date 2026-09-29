from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.v3_android_offline import OfflineCacheStore, policy


class V3AndroidOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = OfflineCacheStore(self.root)
        self.selection = [
            {"type": "document", "id": "doc-1"},
            {"type": "task", "id": "task-1"},
        ]
        self.store.save_workset(
            "alice",
            "trip",
            "main-server",
            self.selection,
            retention_seconds=120,
            now=1000,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_policy_is_explicit_and_unknown_classes_are_not_offline(self):
        values = policy()
        self.assertEqual("offline_allowed", values["document"])
        self.assertEqual("offline_allowed", values["task"])
        self.assertEqual("online_only", values["contact"])
        self.assertNotIn("account", values)

    def test_cache_requires_explicit_selection_and_bounded_schema(self):
        item = self.store.cache_item(
            "alice",
            "trip",
            "document",
            "doc-1",
            server_version="v4",
            payload={"title": "Plan", "mime": "application/pdf", "size": 42},
            now=1001,
        )
        self.assertEqual("v4", item["server_version"])
        self.assertEqual("Plan", item["payload"]["title"])
        with self.assertRaises(PermissionError):
            self.store.cache_item(
                "alice",
                "trip",
                "document",
                "doc-2",
                server_version="v1",
                payload={"title": "Not selected"},
            )
        with self.assertRaises(ValueError):
            self.store.cache_item(
                "alice",
                "trip",
                "document",
                "doc-1",
                server_version="v5",
                payload={"title": "Plan", "unexpected": "blocked"},
            )

    def test_expired_items_are_not_returned_and_can_be_pruned(self):
        self.store.cache_item(
            "alice",
            "trip",
            "task",
            "task-1",
            server_version="7",
            payload={"title": "Pack", "status": "open"},
            now=1000,
        )
        self.assertEqual(1, len(self.store.items("alice", "trip", now=1119)))
        self.assertEqual([], self.store.items("alice", "trip", now=1121))
        self.assertEqual(1, self.store.prune_expired(now=1121))

    def test_task_status_outbox_is_idempotent_and_conflicts_are_visible(self):
        first = self.store.queue_task_status(
            "alice",
            "trip",
            "task-1",
            "done",
            "server-v7",
            operation_id="op-1",
            now=1010,
        )
        second = self.store.queue_task_status(
            "alice",
            "trip",
            "task-1",
            "done",
            "server-v7",
            operation_id="op-1",
            now=1020,
        )
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertEqual(1, len(self.store.pending("alice")))
        with self.assertRaises(ValueError):
            self.store.queue_task_status(
                "alice",
                "trip",
                "task-1",
                "open",
                "server-v7",
                operation_id="op-1",
            )
        conflict = self.store.resolve(
            "alice",
            "op-1",
            "conflict",
            server_version="server-v8",
            error_code="version_changed",
            now=1030,
        )
        self.assertEqual("conflict", conflict["state"])
        self.assertEqual("server-v8", conflict["server_version"])

    def test_user_scope_locks_other_account_cache(self):
        self.store.save_workset(
            "bob",
            "trip",
            "main-server",
            [{"type": "task", "id": "task-2"}],
        )
        self.store.cache_item(
            "bob",
            "trip",
            "task",
            "task-2",
            server_version="1",
            payload={"title": "Bob", "status": "open"},
        )
        self.assertEqual([], self.store.items("alice", "trip", now=2000))
        self.assertEqual(1, self.store.purge_except("alice"))
        with self.assertRaises(LookupError):
            self.store.cache_item(
                "bob",
                "trip",
                "task",
                "task-2",
                server_version="2",
                payload={"title": "Bob", "status": "done"},
            )


if __name__ == "__main__":
    unittest.main()
