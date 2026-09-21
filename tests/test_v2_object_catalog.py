import hashlib
import tempfile
import unittest
from pathlib import Path

from app.v2.catalog import CatalogState, ObjectCatalog
from app.v2.contracts import ErrorCode, LogicalObjectId, StorageLocation


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class V2ObjectCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = ObjectCatalog(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_register_is_idempotent_and_persists_across_restart(self):
        created = self.catalog.register(
            "doc-1",
            "inbox/example.txt",
            version_id="version-1",
            size=7,
            content_sha256=digest(b"content"),
        )
        repeated = self.catalog.register(
            "doc-1",
            "inbox/example.txt",
            version_id="version-1",
            size=7,
            content_sha256=digest(b"content"),
        )
        reopened = ObjectCatalog(self.root).get("doc-1")

        self.assertTrue(created.ok)
        self.assertTrue(repeated.ok)
        self.assertTrue(reopened.ok)
        self.assertEqual(created.value, repeated.value)
        self.assertEqual(StorageLocation("inbox/example.txt"), reopened.value.location)
        self.assertEqual(LogicalObjectId("doc-1"), reopened.value.object_id)

    def test_active_location_is_unique(self):
        self.assertTrue(self.catalog.register(
            "doc-a",
            "archive/file.txt",
            version_id="v1",
            size=1,
            content_sha256=digest(b"a"),
        ).ok)
        conflict = self.catalog.register(
            "doc-b",
            "archive/file.txt",
            version_id="v2",
            size=1,
            content_sha256=digest(b"b"),
        )
        self.assertFalse(conflict.ok)
        self.assertEqual(ErrorCode.CONFLICT, conflict.error.code)

    def test_move_preserves_identity_and_blob_reference(self):
        created = self.catalog.register(
            "doc-move",
            "inbox/source.txt",
            version_id="blob-version",
            size=4,
            content_sha256=digest(b"move"),
        ).value
        moved = self.catalog.move(
            "doc-move",
            "archive/moved.txt",
            expected_version_id="blob-version",
        )
        stale = self.catalog.move(
            "doc-move",
            "archive/other.txt",
            expected_version_id="old-version",
        )

        self.assertTrue(moved.ok)
        self.assertEqual(created.object_id, moved.value.object_id)
        self.assertEqual(created.version_id, moved.value.version_id)
        self.assertEqual(StorageLocation("archive/moved.txt"), moved.value.location)
        self.assertFalse(stale.ok)
        self.assertEqual(ErrorCode.CONFLICT, stale.error.code)

    def test_content_update_requires_expected_version_when_supplied(self):
        self.catalog.register(
            "doc-update",
            "docs/update.bin",
            version_id="v1",
            size=3,
            content_sha256=digest(b"one"),
        )
        stale = self.catalog.update_content(
            "doc-update",
            version_id="v2",
            size=3,
            content_sha256=digest(b"two"),
            expected_version_id="wrong",
        )
        updated = self.catalog.update_content(
            "doc-update",
            version_id="v2",
            size=3,
            content_sha256=digest(b"two"),
            expected_version_id="v1",
        )

        self.assertFalse(stale.ok)
        self.assertEqual(ErrorCode.CONFLICT, stale.error.code)
        self.assertTrue(updated.ok)
        self.assertEqual("v2", updated.value.version_id)
        self.assertEqual(digest(b"two"), updated.value.content_sha256)

    def test_delete_frees_location_but_restore_never_overwrites_new_owner(self):
        self.catalog.register(
            "doc-old",
            "shared/name.txt",
            version_id="old-version",
            size=3,
            content_sha256=digest(b"old"),
        )
        deleted = self.catalog.mark_deleted("doc-old", expected_version_id="old-version")
        replacement = self.catalog.register(
            "doc-new",
            "shared/name.txt",
            version_id="new-version",
            size=3,
            content_sha256=digest(b"new"),
        )
        blocked_restore = self.catalog.restore("doc-old")
        restored_elsewhere = self.catalog.restore("doc-old", location="recovered/name.txt")

        self.assertTrue(deleted.ok)
        self.assertEqual(CatalogState.DELETED, deleted.value.state)
        self.assertTrue(replacement.ok)
        self.assertFalse(blocked_restore.ok)
        self.assertEqual(ErrorCode.CONFLICT, blocked_restore.error.code)
        self.assertTrue(restored_elsewhere.ok)
        self.assertEqual(CatalogState.ACTIVE, restored_elsewhere.value.state)
        self.assertEqual(StorageLocation("recovered/name.txt"), restored_elsewhere.value.location)

    def test_recovery_state_reserves_location_and_can_return_active(self):
        self.catalog.register(
            "doc-recovery",
            "docs/recovery.txt",
            version_id="v1",
            size=4,
            content_sha256=digest(b"data"),
        )
        recovery = self.catalog.mark_recovery("doc-recovery")
        conflict = self.catalog.register(
            "other",
            "docs/recovery.txt",
            version_id="v2",
            size=5,
            content_sha256=digest(b"other"),
        )
        active = self.catalog.mark_active("doc-recovery")

        self.assertTrue(recovery.ok)
        self.assertEqual(CatalogState.RECOVERY, recovery.value.state)
        self.assertFalse(conflict.ok)
        self.assertEqual(ErrorCode.CONFLICT, conflict.error.code)
        self.assertTrue(active.ok)
        self.assertEqual(CatalogState.ACTIVE, active.value.state)

    def test_deleted_entries_are_hidden_by_default_but_auditable(self):
        self.catalog.register(
            "doc-deleted",
            "docs/deleted.txt",
            version_id="v1",
            size=1,
            content_sha256=digest(b"x"),
        )
        self.catalog.mark_deleted("doc-deleted")

        self.assertFalse(self.catalog.get("doc-deleted").ok)
        tombstone = self.catalog.get("doc-deleted", include_deleted=True)
        self.assertTrue(tombstone.ok)
        self.assertEqual(CatalogState.DELETED, tombstone.value.state)
        self.assertEqual([], self.catalog.list())
        self.assertEqual(1, len(self.catalog.list(include_deleted=True)))


if __name__ == "__main__":
    unittest.main()
