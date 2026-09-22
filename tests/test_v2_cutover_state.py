import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.document_store import DocumentStore
from app.v2.adapters.shadow import ShadowDocumentStorageAdapter
from app.v2.contracts import LogicalObjectId, StorageLocation
from app.v2.cutover import (
    cutover_status,
    load_cutover_state,
    mark_shadow_dirty,
    prepare_shadow,
    return_to_v1,
    verify_shadow_consistency,
)
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.recovery_cli import main


class V2CutoverStateTests(unittest.TestCase):
    def _migrated_root(self, base: Path) -> tuple[Path, Path]:
        root = base / "documents"
        root.mkdir()
        payload = b"verified-cutover"
        document = root / "inbox" / "file.bin"
        document.parent.mkdir()
        document.write_bytes(payload)
        metadata = root / ".simpleoffice-meta" / "documents"
        metadata.mkdir(parents=True)
        (metadata / "doc-cutover.json").write_text(
            json.dumps({
                "document_id": "doc-cutover",
                "last_path": "inbox/file.bin",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }),
            encoding="utf-8",
        )
        backup = base / "backup"
        create_migration_backup(root, backup)
        transfer_legacy_documents(root, backup)
        return root, backup

    def test_missing_state_is_v1_and_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = sorted(str(item.relative_to(root)) for item in root.rglob("*"))

            state = load_cutover_state(root)
            status = cutover_status(root)

            after = sorted(str(item.relative_to(root)) for item in root.rglob("*"))
            self.assertEqual("v1", state.mode)
            self.assertFalse(status["verification_ready"])
            self.assertFalse(status["ready_for_v2_activation"])
            self.assertEqual(before, after)
            self.assertFalse((root / ".simpleoffice-v2" / "storage-cutover.json").exists())

    def test_shadow_requires_verification_and_preview_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "clean migration verification"):
                prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
            self.assertFalse((root / ".simpleoffice-v2" / "storage-cutover.json").exists())

            base = Path(temp) / "ready"
            base.mkdir()
            root, _ = self._migrated_root(base)
            preview = prepare_shadow(root, apply=False)
            self.assertFalse(preview["applied"])
            self.assertFalse((root / ".simpleoffice-v2" / "storage-cutover.json").exists())

    def test_shadow_state_is_explicit_atomic_and_fingerprint_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)

            result = prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
            state = load_cutover_state(root)
            status = cutover_status(root)

            self.assertTrue(result["applied"])
            self.assertEqual("shadow", state.mode)
            self.assertEqual(result["migration_fingerprint"], state.migration_fingerprint)
            self.assertTrue(status["verification_ready"])
            self.assertTrue(status["fingerprint_matches"])
            self.assertTrue(status["ready_for_v2_activation"])
            self.assertEqual("local-plaintext", state.protection_mode)
            self.assertFalse(status["encrypted_at_rest"])
            self.assertFalse(status["federation_storage_allowed"])
            path = root / ".simpleoffice-v2" / "storage-cutover.json"
            self.assertTrue(path.is_file())
            if os.name == "posix":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_shadow_dirty_and_v1_return_never_delete_v2_data(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)
            prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
            blob_store = root / ".simpleoffice-v2" / "blob-store"
            catalog = root / ".simpleoffice-v2" / "catalog.sqlite3"
            self.assertTrue(blob_store.is_dir())
            self.assertTrue(catalog.is_file())

            dirty = mark_shadow_dirty(root, "synthetic mirror failure")
            self.assertTrue(dirty.dirty)
            self.assertEqual("synthetic mirror failure", dirty.dirty_reason)

            preview = return_to_v1(root, apply=False)
            self.assertFalse(preview["applied"])
            self.assertEqual("shadow", load_cutover_state(root).mode)

            applied = return_to_v1(root, apply=True)
            self.assertTrue(applied["v2_data_retained"])
            self.assertEqual("v1", load_cutover_state(root).mode)
            self.assertTrue(blob_store.is_dir())
            self.assertTrue(catalog.is_file())

    def test_live_shadow_mutation_is_verified_and_can_rebaseline_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)
            prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
            original_fingerprint = load_cutover_state(root).migration_fingerprint
            adapter = ShadowDocumentStorageAdapter(root, "test-user")

            created = adapter.create_bytes(
                StorageLocation("inbox/later.txt"),
                b"later",
            )
            self.assertTrue(created.ok)
            verification = verify_shadow_consistency(root)
            status = cutover_status(root)

            self.assertTrue(verification["ready"])
            self.assertEqual(2, verification["verified_documents"])
            self.assertTrue(status["verification_ready"])
            self.assertFalse(status["fingerprint_matches"])
            self.assertFalse(status["ready_for_v2_activation"])
            self.assertNotEqual(original_fingerprint, verification["fingerprint"])

            refreshed = prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
            self.assertTrue(refreshed["applied"])
            refreshed_status = cutover_status(root)
            self.assertTrue(refreshed_status["fingerprint_matches"])
            self.assertTrue(refreshed_status["ready_for_v2_activation"])

    def test_shadow_verification_accepts_recoverable_delete_tombstone(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)
            prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
            adapter = ShadowDocumentStorageAdapter(root, "test-user")
            metadata = DocumentStore(root).get_document("doc-cutover")

            deleted = adapter.delete(
                LogicalObjectId("doc-cutover"),
                expected_version=metadata["sha256"],
            )
            self.assertTrue(deleted.ok)
            verification = verify_shadow_consistency(root)

            self.assertTrue(verification["ready"])
            self.assertEqual(0, verification["active_documents"])
            self.assertEqual(1, verification["deleted_documents"])
            self.assertEqual(1, verification["verified_documents"])

    def test_direct_v1_mutation_is_visible_as_shadow_divergence(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)
            prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)

            DocumentStore(root).create_document_at(
                "inbox/bypass.txt",
                b"bypass",
                "legacy-direct",
            )
            verification = verify_shadow_consistency(root)

            self.assertFalse(verification["ready"])
            self.assertTrue(
                any("catalog object is missing" in item for item in verification["blockers"])
            )

    def test_malformed_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / ".simpleoffice-v2" / "storage-cutover.json"
            state.parent.mkdir()
            state.write_text('{"format":"wrong","format_version":1,"mode":"shadow"}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_cutover_state(root)

    def test_shadow_apply_requires_explicit_plaintext_acknowledgement(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)

            with self.assertRaisesRegex(ValueError, "acknowledgement"):
                prepare_shadow(root, apply=True)
            self.assertEqual("v1", load_cutover_state(root).mode)

    def test_recovery_cli_requires_apply_for_shadow_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)

            with redirect_stdout(StringIO()) as output:
                preview_code = main(["--root", str(root), "storage-shadow"])
            self.assertEqual(3, preview_code)
            self.assertFalse(json.loads(output.getvalue())["applied"])

            with redirect_stdout(StringIO()) as output:
                refused_code = main(["--root", str(root), "storage-shadow", "--apply"])
            self.assertEqual(3, refused_code)
            self.assertIn("acknowledge-local-plaintext", output.getvalue())
            self.assertEqual("v1", load_cutover_state(root).mode)

            with redirect_stdout(StringIO()) as output:
                applied_code = main([
                    "--root", str(root), "storage-shadow", "--apply",
                    "--acknowledge-local-plaintext",
                ])
            self.assertEqual(0, applied_code)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["applied"])
            self.assertEqual("local-plaintext", payload["protection_mode"])
            self.assertFalse(payload["federation_storage_allowed"])
            self.assertEqual("shadow", load_cutover_state(root).mode)

    def test_recovery_cli_requires_apply_and_ack_for_v2_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)
            prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)

            with redirect_stdout(StringIO()) as output:
                preview_code = main(["--root", str(root), "storage-v2"])
            self.assertEqual(3, preview_code)
            self.assertFalse(json.loads(output.getvalue())["applied"])
            self.assertEqual("shadow", load_cutover_state(root).mode)

            with redirect_stdout(StringIO()) as output:
                refused_code = main(["--root", str(root), "storage-v2", "--apply"])
            self.assertEqual(3, refused_code)
            self.assertIn("acknowledge-local-plaintext", output.getvalue())
            self.assertEqual("shadow", load_cutover_state(root).mode)

            with redirect_stdout(StringIO()) as output:
                applied_code = main([
                    "--root", str(root), "storage-v2", "--apply",
                    "--acknowledge-local-plaintext",
                ])
            self.assertEqual(0, applied_code)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["applied"])
            self.assertEqual("v2", load_cutover_state(root).mode)


if __name__ == "__main__":
    unittest.main()
