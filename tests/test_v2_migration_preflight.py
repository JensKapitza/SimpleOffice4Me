import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.v2.blob_store import BlobStore
from app.v2.contracts import LogicalObjectId
from app.v2.migration import build_migration_plan, create_migration_backup, inspect_migration, transfer_legacy_documents
from app.v2.recovery_cli import main


class V2MigrationPreflightTests(unittest.TestCase):
    def test_missing_root_is_blocked_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "missing"
            result = inspect_migration(root)
            self.assertFalse(result.ready)
            self.assertFalse(root.exists())
            self.assertIn("document root does not exist", result.blockers)

    def test_empty_existing_root_is_read_only_and_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            result = inspect_migration(root)
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            self.assertTrue(result.ready)
            self.assertEqual(before, after)
            self.assertEqual(0, result.v2_invalid_objects)

    def test_cli_preflight_does_not_construct_v2_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--root", str(root), "migration-preflight"])
            self.assertEqual(0, code)
            self.assertFalse((root / ".simpleoffice-v2").exists())
            self.assertTrue(json.loads(output.getvalue())["ready"])

    def test_backup_copies_source_and_writes_manifest_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            (root / "invoice.txt").write_text("content", encoding="utf-8")
            (root / ".simpleoffice-meta").mkdir()
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            target = base / "backup"

            result = create_migration_backup(root, target)

            after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertEqual("content", (target / "invoice.txt").read_text(encoding="utf-8"))
            manifest = json.loads((target / ".simpleoffice-v2" / "migration-backup.json").read_text(encoding="utf-8"))
            self.assertEqual("simpleoffice-v2-migration-backup", manifest["format"])
            self.assertEqual(1, manifest["files"])
            self.assertEqual(str(target.resolve()), result["destination"])

    def test_backup_refuses_destination_inside_source_or_existing_target(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "outside the source tree"):
                create_migration_backup(root, root / "backup")

            target = base / "backup"
            target.mkdir()
            with self.assertRaises(FileExistsError):
                create_migration_backup(root, target)

    def test_cli_backup_requires_explicit_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            target = base / "backup"
            output = StringIO()
            with redirect_stdout(output):
                code = main([
                    "--root", str(root), "migration-backup",
                    "--destination", str(target),
                ])
            self.assertEqual(3, code)
            self.assertFalse(target.exists())
            self.assertIn("--apply", output.getvalue())


    def test_migration_plan_is_read_only_and_verifies_legacy_documents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            content = b"legacy-content"
            document = root / "inbox" / "invoice.txt"
            document.parent.mkdir()
            document.write_bytes(content)
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            metadata = {
                "document_id": "doc-1",
                "last_path": "inbox/invoice.txt",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            (metadata_dir / "doc-1.json").write_text(json.dumps(metadata), encoding="utf-8")
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

            plan = build_migration_plan(root)

            after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertTrue(plan["ready"])
            self.assertEqual(1, plan["ready_documents"])
            self.assertEqual(len(content), plan["bytes"])
            self.assertEqual("ready", plan["entries"][0]["status"])
            self.assertFalse((root / ".simpleoffice-v2").exists())

    def test_migration_plan_blocks_hash_mismatch_and_cli_reports_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = root / "inbox" / "invoice.txt"
            document.parent.mkdir()
            document.write_bytes(b"changed")
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / "doc-1.json").write_text(
                json.dumps({
                    "document_id": "doc-1",
                    "last_path": "inbox/invoice.txt",
                    "sha256": "0" * 64,
                }),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--root", str(root), "migration-plan"])
            report = json.loads(output.getvalue())
            self.assertEqual(2, code)
            self.assertFalse(report["ready"])
            self.assertEqual(1, report["blocked_documents"])
            self.assertIn("does not match", report["entries"][0]["error"])


    def test_transfer_is_idempotent_and_keeps_v1_content_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            content = b"legacy-document-content"
            document = root / "inbox" / "invoice.txt"
            document.parent.mkdir()
            document.write_bytes(content)
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            metadata = {
                "document_id": "doc-transfer-1",
                "last_path": "inbox/invoice.txt",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            (metadata_dir / "doc-transfer-1.json").write_text(json.dumps(metadata), encoding="utf-8")
            backup = base / "backup"
            create_migration_backup(root, backup)
            before_content = document.read_bytes()
            before_metadata = (metadata_dir / "doc-transfer-1.json").read_bytes()

            first = transfer_legacy_documents(root, backup)
            second = transfer_legacy_documents(root, backup)

            object_id = LogicalObjectId("doc-transfer-1")
            store = BlobStore(root)
            self.assertEqual(content, store.read(object_id))
            self.assertEqual(1, len(store.versions_for(object_id)))
            self.assertEqual(1, first["migrated_documents"])
            self.assertEqual(0, first["already_present_documents"])
            self.assertEqual(0, second["migrated_documents"])
            self.assertEqual(1, second["already_present_documents"])
            self.assertEqual(before_content, document.read_bytes())
            self.assertEqual(before_metadata, (metadata_dir / "doc-transfer-1.json").read_bytes())
            report = json.loads((root / ".simpleoffice-v2" / "migration-transfer.json").read_text(encoding="utf-8"))
            self.assertEqual("content-copied", report["status"])

    def test_transfer_rejects_backup_that_no_longer_matches_source_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            content = b"original"
            document = root / "inbox" / "file.bin"
            document.parent.mkdir()
            document.write_bytes(content)
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / "doc.json").write_text(
                json.dumps({
                    "document_id": "doc-backup-check",
                    "last_path": "inbox/file.bin",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }),
                encoding="utf-8",
            )
            backup = base / "backup"
            create_migration_backup(root, backup)
            (backup / "inbox" / "file.bin").write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "backup integrity mismatch"):
                transfer_legacy_documents(root, backup)
            self.assertFalse((root / ".simpleoffice-v2" / "blob-store").exists())

    def test_transfer_refuses_existing_v2_content_conflict_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            content = b"legacy"
            document = root / "inbox" / "file.bin"
            document.parent.mkdir()
            document.write_bytes(content)
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / "doc.json").write_text(
                json.dumps({
                    "document_id": "doc-conflict",
                    "last_path": "inbox/file.bin",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }),
                encoding="utf-8",
            )
            backup = base / "backup"
            create_migration_backup(root, backup)
            store = BlobStore(root)
            store.write(LogicalObjectId("doc-conflict"), b"different")

            with self.assertRaisesRegex(ValueError, "conflicts"):
                transfer_legacy_documents(root, backup)
            self.assertEqual(b"different", store.read(LogicalObjectId("doc-conflict")))
            self.assertEqual(1, len(store.versions_for(LogicalObjectId("doc-conflict"))))

    def test_cli_transfer_requires_apply_and_then_copies_content(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            content = b"cli-transfer"
            document = root / "inbox" / "file.bin"
            document.parent.mkdir()
            document.write_bytes(content)
            metadata_dir = root / ".simpleoffice-meta" / "documents"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / "doc.json").write_text(
                json.dumps({
                    "document_id": "doc-cli-transfer",
                    "last_path": "inbox/file.bin",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }),
                encoding="utf-8",
            )
            backup = base / "backup"
            create_migration_backup(root, backup)
            with redirect_stdout(StringIO()) as output:
                code = main([
                    "--root", str(root), "migration-transfer",
                    "--backup", str(backup),
                ])
            self.assertEqual(3, code)
            self.assertIn("--apply", output.getvalue())
            self.assertFalse((root / ".simpleoffice-v2" / "blob-store").exists())

            with redirect_stdout(StringIO()) as output:
                code = main([
                    "--root", str(root), "migration-transfer",
                    "--backup", str(backup), "--apply",
                ])
            self.assertEqual(0, code)
            report = json.loads(output.getvalue())
            self.assertEqual(1, report["migrated_documents"])
            self.assertEqual(content, BlobStore(root).read(LogicalObjectId("doc-cli-transfer")))


    def test_preflight_reports_corrupted_v2_blob_as_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = BlobStore(root, chunk_size=64 * 1024)
            version = store.write(LogicalObjectId("corrupt-v2"), b"payload")
            manifest = store.version_manifest(version.version_id)
            store._chunk_path(manifest["chunks"][0]["physical_id"]).write_bytes(b"tampered")

            result = inspect_migration(root)

            self.assertFalse(result.ready)
            self.assertEqual(1, result.v2_invalid_objects)
            self.assertTrue(any("integrity verification" in blocker for blocker in result.blockers))


if __name__ == "__main__":
    unittest.main()
