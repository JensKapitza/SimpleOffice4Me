from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.v2.adapters.authoritative import V2AuthoritativeStorageAdapter
from app.v2.legacy_cleanup import legacy_cleanup_status


class V2LegacyCleanupReadinessTests(unittest.TestCase):
    def test_v1_root_is_blocked_and_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

            status = legacy_cleanup_status(root)

            after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            self.assertFalse(status["ready_for_cleanup"])
            self.assertFalse(status["deletion_supported"])
            self.assertEqual("v1", status["mode"])
            self.assertTrue(
                any("authoritative V2 mode" in item for item in status["blockers"])
            )
            self.assertEqual(before, after)

    def test_encrypted_v2_still_blocks_while_projection_adapter_requires_legacy_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            control = root / ".simpleoffice-v2"
            control.mkdir()
            (control / "storage-cutover.json").write_text(
                json.dumps({
                    "format": "simpleoffice-v2-storage-cutover",
                    "format_version": 1,
                    "mode": "v2",
                    "protection_mode": "local-encrypted-blob",
                    "migration_fingerprint": "",
                    "verified_at": "2026-09-24T10:00:00Z",
                    "updated_at": "2026-09-24T10:00:00Z",
                    "dirty": False,
                    "dirty_reason": "",
                }),
                encoding="utf-8",
            )

            status = legacy_cleanup_status(root)

            self.assertTrue(
                V2AuthoritativeStorageAdapter.requires_legacy_projection
            )
            self.assertFalse(status["ready_for_cleanup"])
            self.assertTrue(status["compatibility_projection_required"])
            self.assertFalse(status["projection_free_cutover_ready"])
            self.assertGreaterEqual(
                len(status["remaining_content_projection_consumers"]),
                4,
            )
            self.assertFalse(
                any(
                    "video transcod" in item.casefold()
                    for item in status["remaining_content_projection_consumers"]
                )
            )
            self.assertEqual(
                [
                    "authoritative storage still requires the legacy DocumentStore compatibility projection"
                ],
                status["blockers"],
            )

    def test_inventory_reports_retained_plaintext_targets_without_deleting_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / ".simpleoffice-meta" / "documents"
            metadata.mkdir(parents=True)
            (metadata / "one.json").write_text("{}", encoding="utf-8")
            (metadata / "two.json").write_text("{}", encoding="utf-8")
            blob = root / ".simpleoffice-v2" / "blob-store"
            blob.mkdir(parents=True)
            (blob / "marker.bin").write_bytes(b"retained")
            before = {
                str(path.relative_to(root)): (
                    path.read_bytes() if path.is_file() else None
                )
                for path in root.rglob("*")
            }

            status = legacy_cleanup_status(root)

            after = {
                str(path.relative_to(root)): (
                    path.read_bytes() if path.is_file() else None
                )
                for path in root.rglob("*")
            }
            self.assertTrue(status["legacy_document_metadata_present"])
            self.assertEqual(2, status["legacy_document_metadata_files"])
            self.assertTrue(status["plaintext_v2_blob_store_present"])
            self.assertEqual(before, after)

    def test_unreadable_pending_rotation_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            control = root / ".simpleoffice-v2"
            control.mkdir()
            (control / "storage-master-key-rotation.json").write_text(
                "not-json",
                encoding="utf-8",
            )

            status = legacy_cleanup_status(root)

            self.assertTrue(status["storage_key_rotation_pending"])
            self.assertTrue(
                any("rotation state is unreadable" in item for item in status["blockers"])
            )


if __name__ == "__main__":
    unittest.main()
