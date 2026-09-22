import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.v2.cutover import (
    cutover_status,
    load_cutover_state,
    mark_shadow_dirty,
    prepare_shadow,
    return_to_v1,
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
                prepare_shadow(root, apply=True)
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

            result = prepare_shadow(root, apply=True)
            state = load_cutover_state(root)
            status = cutover_status(root)

            self.assertTrue(result["applied"])
            self.assertEqual("shadow", state.mode)
            self.assertEqual(result["migration_fingerprint"], state.migration_fingerprint)
            self.assertTrue(status["verification_ready"])
            self.assertTrue(status["fingerprint_matches"])
            self.assertFalse(status["ready_for_v2_activation"])
            path = root / ".simpleoffice-v2" / "storage-cutover.json"
            self.assertTrue(path.is_file())
            if os.name == "posix":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_shadow_dirty_and_v1_return_never_delete_v2_data(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)
            prepare_shadow(root, apply=True)
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

    def test_malformed_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / ".simpleoffice-v2" / "storage-cutover.json"
            state.parent.mkdir()
            state.write_text('{"format":"wrong","format_version":1,"mode":"shadow"}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_cutover_state(root)

    def test_recovery_cli_requires_apply_for_shadow_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, _ = self._migrated_root(base)

            with redirect_stdout(StringIO()) as output:
                preview_code = main(["--root", str(root), "storage-shadow"])
            self.assertEqual(3, preview_code)
            self.assertFalse(json.loads(output.getvalue())["applied"])

            with redirect_stdout(StringIO()) as output:
                applied_code = main(["--root", str(root), "storage-shadow", "--apply"])
            self.assertEqual(0, applied_code)
            self.assertTrue(json.loads(output.getvalue())["applied"])
            self.assertEqual("shadow", load_cutover_state(root).mode)


if __name__ == "__main__":
    unittest.main()
