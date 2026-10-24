"""Regression coverage for public shares after a physical collection MOVE."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import file_lock
from app.document_store import DocumentStore


class CollectionMoveShareRevocationTest(unittest.TestCase):
    def test_nested_collection_move_invalidates_existing_share_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source" / "nested").mkdir(parents=True)
            (root / "source" / "first.txt").write_text("first", encoding="utf-8")
            (root / "source" / "nested" / "second.txt").write_text("second", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            documents = [
                store.get_document("source/first.txt"),
                store.get_document("source/nested/second.txt"),
            ]
            password = "a-long-test-password"
            shares = [
                store.create_share(doc["document_id"], password, 1, "admin")
                for doc in documents
            ]
            for share in shares:
                self.assertTrue(store.open_share(share["share_id"], password))
            store.move_collection("source", "destination", "admin")
            for doc, share in zip(documents, shares):
                moved = store.get_document(doc["document_id"])
                self.assertEqual(doc.get("location_revision", 0) + 1, moved["location_revision"])
                self.assertTrue(moved["location_last_moved_at"])
                self.assertTrue(moved["last_path"].startswith("destination/"))
                with self.assertRaises(ValueError):
                    store.open_share(share["share_id"], password)
            events = [
                json.loads(line)
                for line in store.events.read_text(encoding="utf-8").splitlines()
            ]
            revoked = [event for event in events if event.get("type") == "share_revoked_on_move"]
            self.assertEqual(
                {share["share_id"] for share in shares},
                {event["share_id"] for event in revoked},
            )

    def test_collection_move_serializes_share_snapshot_and_location_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source").mkdir()
            (root / "source" / "record.txt").write_text("record", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            document = store.get_document("source/record.txt")
            store.create_share(document["document_id"], "a-long-test-password", 1, "admin")

            share_lock = store.shares_path.with_suffix(".lock")
            held_locks: set[Path] = set()
            original_lock = file_lock.exclusive_file_lock

            @contextmanager
            def track_locks(path, **kwargs):
                lock_path = Path(path)
                with original_lock(path, **kwargs) as acquired:
                    if acquired:
                        held_locks.add(lock_path)
                    try:
                        yield acquired
                    finally:
                        held_locks.discard(lock_path)

            original_share_snapshot = store._active_share_ids_for_move
            original_event = store._event

            def verify_share_lock(document_id):
                self.assertIn(share_lock, held_locks)
                return original_share_snapshot(document_id)

            original_save = store._save_document

            def verify_location_write_is_locked(metadata):
                if str(metadata.get("last_path", "")).startswith("destination/"):
                    self.assertIn(share_lock, held_locks)
                return original_save(metadata)

            def verify_share_audit_is_locked(event_type, details):
                if event_type == "share_revoked_on_move":
                    self.assertIn(share_lock, held_locks)
                return original_event(event_type, details)

            with patch(
                "app.file_lock.exclusive_file_lock", side_effect=track_locks,
            ), patch.object(
                store, "_active_share_ids_for_move", side_effect=verify_share_lock,
            ), patch.object(
                store, "_save_document", side_effect=verify_location_write_is_locked,
            ), patch.object(
                store, "_event", side_effect=verify_share_audit_is_locked,
            ):
                store.move_collection("source", "destination", "admin")

    def test_metadata_write_failure_rolls_back_move_without_revoking_shares(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source" / "nested").mkdir(parents=True)
            (root / "source" / "first.txt").write_text("first", encoding="utf-8")
            (root / "source" / "nested" / "second.txt").write_text("second", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            documents = [
                store.get_document("source/first.txt"),
                store.get_document("source/nested/second.txt"),
            ]
            password = "a-long-test-password"
            shares = [
                store.create_share(doc["document_id"], password, 1, "admin")
                for doc in documents
            ]
            before = {
                doc["document_id"]: store.get_document(doc["document_id"])
                for doc in documents
            }
            fail_document_id = documents[1]["document_id"]
            failure_raised = False
            original_save = store._save_document

            def fail_destination_metadata_once(metadata):
                nonlocal failure_raised
                if (
                    not failure_raised
                    and metadata.get("document_id") == fail_document_id
                    and str(metadata.get("last_path", "")).startswith("destination/")
                ):
                    failure_raised = True
                    raise OSError("simulated metadata write failure")
                return original_save(metadata)

            with patch.object(store, "_save_document", side_effect=fail_destination_metadata_once):
                with self.assertRaisesRegex(OSError, "simulated metadata write failure"):
                    store.move_collection("source", "destination", "admin")

            self.assertTrue(failure_raised)
            self.assertTrue((root / "source" / "first.txt").is_file())
            self.assertTrue((root / "source" / "nested" / "second.txt").is_file())
            self.assertFalse((root / "destination").exists())
            for original, share in zip(documents, shares):
                restored = store.get_document(original["document_id"])
                self.assertEqual(original["last_path"], restored["last_path"])
                self.assertEqual(
                    store._location_revision(before[original["document_id"]]),
                    store._location_revision(restored),
                )
                self.assertEqual(
                    before[original["document_id"]].get("location_last_moved_at"),
                    restored.get("location_last_moved_at"),
                )
                opened = store.open_share(share["share_id"], password)
                self.assertEqual(original["last_path"], store.relative(opened["path"]))
            events = [
                json.loads(line)
                for line in store.events.read_text(encoding="utf-8").splitlines()
            ]
            self.assertFalse(any(
                event.get("type") == "share_revoked_on_move" for event in events
            ))


if __name__ == "__main__":
    unittest.main()
