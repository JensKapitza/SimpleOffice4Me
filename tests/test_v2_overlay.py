import tempfile
import unittest
from pathlib import Path

from app.v2.adapters.document_store import DocumentStoreStorageAdapter
from app.v2.contracts import ErrorCode, OperationResult, StorageLocation, StoredObject, LogicalObjectId
from app.v2.overlay import OverlayImportJournal, OverlayImportState


class FailingStorage:
    def __init__(self, *, raise_error=False, retryable=False):
        self.raise_error = raise_error
        self.retryable = retryable

    def create_bytes(self, location, content):
        if self.raise_error:
            raise RuntimeError("synthetic storage failure")
        if self.retryable:
            return OperationResult.failure(
                ErrorCode.STORAGE_UNAVAILABLE,
                "synthetic unavailable",
                retryable=True,
            )
        return OperationResult.success(
            StoredObject(
                object_id=LogicalObjectId("synthetic-object"),
                version="synthetic-version",
                size=len(content),
                location=location,
            )
        )


class OverlayImportJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "docs").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_stage_process_commit_and_cleanup(self):
        storage = DocumentStoreStorageAdapter(self.root, "overlay-test")
        journal = OverlayImportJournal(self.root, storage)
        staged = journal.stage_bytes(StorageLocation("docs/file.txt"), b"hello")
        self.assertEqual(OverlayImportState.PENDING, staged.state)
        staging = journal.staging / f"{staged.import_id}.bin"
        self.assertTrue(staging.exists())

        committed = journal.process(staged.import_id)
        self.assertEqual(OverlayImportState.COMMITTED, committed.state)
        self.assertIsNotNone(committed.object_id)
        self.assertFalse(staging.exists())
        self.assertEqual(b"hello", storage.read_bytes(committed.object_id).value)

    def test_staging_integrity_failure_becomes_damaged(self):
        journal = OverlayImportJournal(self.root, FailingStorage())
        staged = journal.stage_bytes(StorageLocation("docs/file.txt"), b"hello")
        (journal.staging / f"{staged.import_id}.bin").write_bytes(b"changed")

        damaged = journal.process(staged.import_id)
        self.assertEqual(OverlayImportState.DAMAGED, damaged.state)
        self.assertIn("integrity", damaged.last_error)

    def test_retryable_storage_failure_returns_to_pending(self):
        journal = OverlayImportJournal(self.root, FailingStorage(retryable=True))
        staged = journal.stage_bytes(StorageLocation("docs/file.txt"), b"hello")

        retry = journal.process(staged.import_id)
        self.assertEqual(OverlayImportState.PENDING, retry.state)
        self.assertIn("synthetic unavailable", retry.last_error)

    def test_unknown_storage_outcome_requires_reconciliation(self):
        journal = OverlayImportJournal(self.root, FailingStorage(raise_error=True))
        staged = journal.stage_bytes(StorageLocation("docs/file.txt"), b"hello")

        recovery = journal.process(staged.import_id)
        self.assertEqual(OverlayImportState.RECOVERY_NEEDED, recovery.state)
        with self.assertRaises(ValueError):
            journal.process(staged.import_id)
        with self.assertRaises(ValueError):
            journal.retry_after_reconciliation(
                staged.import_id,
                confirmed_not_committed=False,
            )

        pending = journal.retry_after_reconciliation(
            staged.import_id,
            confirmed_not_committed=True,
        )
        self.assertEqual(OverlayImportState.PENDING, pending.state)

    def test_interrupted_processing_is_marked_recovery_needed(self):
        journal = OverlayImportJournal(self.root, FailingStorage())
        staged = journal.stage_bytes(StorageLocation("docs/file.txt"), b"hello")
        journal._set_state(staged.import_id, OverlayImportState.PROCESSING)

        recovered = journal.recover_incomplete()
        self.assertEqual(1, len(recovered))
        self.assertEqual(OverlayImportState.RECOVERY_NEEDED, recovered[0].state)

    def test_acknowledge_committed_requires_matching_target(self):
        journal = OverlayImportJournal(self.root, FailingStorage(raise_error=True))
        staged = journal.stage_bytes(StorageLocation("docs/file.txt"), b"hello")
        journal.process(staged.import_id)

        wrong = StoredObject(
            LogicalObjectId("object-1"),
            "version-1",
            5,
            StorageLocation("docs/other.txt"),
        )
        with self.assertRaises(ValueError):
            journal.acknowledge_committed(staged.import_id, wrong)

        correct = StoredObject(
            LogicalObjectId("object-1"),
            "version-1",
            5,
            StorageLocation("docs/file.txt"),
        )
        committed = journal.acknowledge_committed(staged.import_id, correct)
        self.assertEqual(OverlayImportState.COMMITTED, committed.state)
        self.assertFalse((journal.staging / f"{staged.import_id}.bin").exists())

    def test_rollback_only_deletes_safely_uncommitted_staging(self):
        journal = OverlayImportJournal(self.root, FailingStorage())
        staged = journal.stage_bytes(StorageLocation("docs/file.txt"), b"hello")
        journal.rollback_staged(staged.import_id)
        with self.assertRaises(KeyError):
            journal.get(staged.import_id)


if __name__ == "__main__":
    unittest.main()
