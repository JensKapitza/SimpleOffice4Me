import tempfile
import unittest
from pathlib import Path

from tools.backup import create_backup, verify_backup


class BackupTest(unittest.TestCase):
    def test_backup_contains_files_manifest_and_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            (root / "invoice.txt").write_text("stable content", encoding="utf-8")
            (root / "loop").symlink_to(root, target_is_directory=True)
            backup = base / "backup.tar.gz"

            manifest = create_backup(root, backup)
            verification = verify_backup(backup)

            self.assertEqual(["loop"], manifest["skipped_symlinks"])
            self.assertEqual(1, verification["files"])
            self.assertTrue(verification["valid"])

    def test_backup_refuses_destination_inside_document_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                create_backup(root, root / "backup.tar.gz")

    def test_backup_never_overwrites_an_existing_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            backup = base / "backup.tar.gz"
            backup.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                create_backup(root, backup)
