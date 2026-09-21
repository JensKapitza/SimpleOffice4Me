import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.v2.migration import build_migration_plan, create_migration_backup, inspect_migration
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


if __name__ == "__main__":
    unittest.main()
