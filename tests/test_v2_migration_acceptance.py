from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from app.v2.blob_store import BlobStore
from app.v2.contracts import LogicalObjectId
from app.v2.cutover import load_cutover_state
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.migration_acceptance import finalize_migration, migration_smoke_test
from app.v2.recovery_cli import main


class V2MigrationAcceptanceTests(unittest.TestCase):
    def _migrated_root(self, base: Path) -> tuple[Path, Path]:
        root = base / "documents"
        root.mkdir()
        content = b"phase-14-smoke-test"
        document = root / "inbox" / "sample.bin"
        document.parent.mkdir()
        document.write_bytes(content)
        metadata_dir = root / ".simpleoffice-meta" / "documents"
        metadata_dir.mkdir(parents=True)
        (metadata_dir / "sample.json").write_text(
            json.dumps({
                "document_id": "phase-14-sample",
                "last_path": "inbox/sample.bin",
                "sha256": hashlib.sha256(content).hexdigest(),
            }),
            encoding="utf-8",
        )
        backup = base / "backup"
        create_migration_backup(root, backup)
        transfer_legacy_documents(root, backup)
        return root, backup

    def test_smoke_test_reads_v2_without_writing_completion_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _backup = self._migrated_root(Path(temp))
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

            result = migration_smoke_test(root)

            after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            self.assertTrue(result["ready"])
            self.assertEqual("phase-14-sample", result["smoke_object_id"])
            self.assertEqual(1, result["verified_documents"])
            self.assertEqual(before, after)
            self.assertFalse(
                (root / ".simpleoffice-v2" / "storage-cutover.json").exists()
            )

    def test_finalize_preview_is_read_only_and_reports_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _backup = self._migrated_root(Path(temp))

            result = finalize_migration(root, apply=False)

            self.assertTrue(result["ready"])
            self.assertFalse(result["applied"])
            self.assertFalse(result["completed"])
            self.assertEqual("v1", result["mode"])
            self.assertFalse(result["completion_marker_present"])
            self.assertEqual("v1", load_cutover_state(root).mode)

    def test_finalize_apply_writes_verified_shadow_completion_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _backup = self._migrated_root(Path(temp))

            result = finalize_migration(
                root,
                apply=True,
                acknowledge_local_plaintext=True,
            )

            state = load_cutover_state(root)
            self.assertTrue(result["ready"])
            self.assertTrue(result["applied"])
            self.assertTrue(result["completed"])
            self.assertEqual("shadow", result["mode"])
            self.assertTrue(result["completion_marker_present"])
            self.assertEqual("shadow", state.mode)
            self.assertEqual(result["migration_fingerprint"], state.migration_fingerprint)
            self.assertTrue(state.migration_fingerprint)

            repeated = finalize_migration(root, apply=False)
            self.assertTrue(repeated["completed"])
            self.assertTrue(repeated["ready"])
            self.assertEqual("shadow", repeated["mode"])
            self.assertEqual("phase-14-sample", repeated["smoke_object_id"])

    def test_finalize_without_plaintext_acknowledgement_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _backup = self._migrated_root(Path(temp))

            result = finalize_migration(root, apply=True)

            self.assertFalse(result["ready"])
            self.assertFalse(result["applied"])
            self.assertFalse(result["completed"])
            self.assertTrue(any("acknowledgement" in value for value in result["blockers"]))
            self.assertEqual("v1", load_cutover_state(root).mode)

    def test_smoke_test_blocks_corrupt_v2_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _backup = self._migrated_root(Path(temp))
            store = BlobStore(root)
            version = store.verify(LogicalObjectId("phase-14-sample"))
            manifest = store.version_manifest(version.version_id)
            chunk = store._chunk_path(str(manifest["chunks"][0]["physical_id"]))
            chunk.write_bytes(b"corrupt")

            result = migration_smoke_test(root)

            self.assertFalse(result["ready"])
            self.assertFalse(result["completed"])
            self.assertTrue(result["blockers"])
            self.assertEqual("v1", load_cutover_state(root).mode)

    def test_cli_smoke_and_finalize_are_preview_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _backup = self._migrated_root(Path(temp))

            with redirect_stdout(StringIO()) as output:
                code = main(["--root", str(root), "migration-smoke"])
            self.assertEqual(0, code)
            self.assertTrue(json.loads(output.getvalue())["ready"])

            with redirect_stdout(StringIO()) as output:
                code = main(["--root", str(root), "migration-finalize"])
            preview = json.loads(output.getvalue())
            self.assertEqual(3, code)
            self.assertTrue(preview["ready"])
            self.assertFalse(preview["completed"])
            self.assertEqual("v1", load_cutover_state(root).mode)

            with redirect_stdout(StringIO()) as output:
                code = main([
                    "--root", str(root),
                    "migration-finalize",
                    "--apply",
                    "--acknowledge-local-plaintext",
                ])
            applied = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertTrue(applied["completed"])
            self.assertEqual("shadow", load_cutover_state(root).mode)

    def test_cli_finalize_without_acknowledgement_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _backup = self._migrated_root(Path(temp))

            with redirect_stdout(StringIO()) as output:
                code = main([
                    "--root", str(root),
                    "migration-finalize",
                    "--apply",
                ])

            result = json.loads(output.getvalue())
            self.assertEqual(2, code)
            self.assertFalse(result["ready"])
            self.assertFalse(result["completed"])
            self.assertEqual("v1", load_cutover_state(root).mode)


    def test_interrupted_transfer_resumes_without_duplicate_or_data_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            contents = {
                "first": b"release-gate-first",
                "second": b"release-gate-second",
            }
            for name, content in contents.items():
                document = root / "inbox" / (name + ".bin")
                document.parent.mkdir(exist_ok=True)
                document.write_bytes(content)
                (metadata_dir / (name + ".json")).write_text(
                    json.dumps({
                        "document_id": "release-gate-" + name,
                        "last_path": "inbox/" + name + ".bin",
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }),
                    encoding="utf-8",
                )
            backup = base / "backup"
            create_migration_backup(root, backup)

            original_write = BlobStore.write_stream
            calls = 0

            def interrupted_write(store, object_id, stream, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated process interruption")
                return original_write(store, object_id, stream, **kwargs)

            with patch.object(BlobStore, "write_stream", interrupted_write):
                with self.assertRaises(OSError):
                    transfer_legacy_documents(root, backup)

            store = BlobStore(root)
            first = store.verify(LogicalObjectId("release-gate-first"))
            self.assertEqual(hashlib.sha256(contents["first"]).hexdigest(), first.content_sha256)
            self.assertFalse((root / ".simpleoffice-v2" / "migration-transfer.json").exists())

            resumed = transfer_legacy_documents(root, backup)
            self.assertEqual(2, resumed["documents"])
            self.assertEqual(1, resumed["already_present_documents"])
            self.assertEqual(1, resumed["migrated_documents"])
            for name, content in contents.items():
                version = store.verify(LogicalObjectId("release-gate-" + name))
                self.assertEqual(hashlib.sha256(content).hexdigest(), version.content_sha256)

            repeated = transfer_legacy_documents(root, backup)
            self.assertEqual(2, repeated["already_present_documents"])
            self.assertEqual(2, repeated["already_cataloged_documents"])
            self.assertEqual(0, repeated["migrated_documents"])
            self.assertEqual(0, repeated["cataloged_documents"])


if __name__ == "__main__":
    unittest.main()
