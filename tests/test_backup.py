import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

from tools.backup import create_backup, verify_backup


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, io.BytesIO(data))


def add_manifest(archive: tarfile.TarFile, files: list[dict[str, object]]) -> None:
    add_bytes(
        archive,
        "SimpleOffice4Me/_simpleoffice_backup_manifest.json",
        json.dumps({"version": 1, "created_at": "test", "files": files}).encode("utf-8"),
    )


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

    @unittest.skipIf(os.name == "nt", "Windows file access is controlled by ACLs")
    def test_backup_permissions_stay_private_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            (root / "private.txt").write_text("confidential", encoding="utf-8")
            backup = base / "backup.tar.gz"
            previous_umask = os.umask(0)
            try:
                create_backup(root, backup)
            finally:
                os.umask(previous_umask)

            self.assertEqual(0o600, stat.S_IMODE(backup.stat().st_mode))

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

    @unittest.skipIf(os.name == "nt", "symbolic-link test requires POSIX semantics")
    def test_backup_does_not_follow_or_remove_dangling_partial_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "documents"
            root.mkdir()
            backup = base / "backup.tar.gz"
            missing_target = base / "must-not-be-created"
            partial = backup.with_name(backup.name + ".partial")
            partial.symlink_to(missing_target)

            with self.assertRaises(FileExistsError):
                create_backup(root, backup)

            self.assertTrue(partial.is_symlink())
            self.assertFalse(missing_target.exists())

    def test_verification_rejects_unmanifested_files(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = Path(temp) / "unexpected.tar.gz"
            content = b"expected"
            files = [
                {"path": "expected.txt", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            ]
            with tarfile.open(backup, "w:gz") as archive:
                add_bytes(archive, "SimpleOffice4Me/expected.txt", content)
                add_bytes(archive, "SimpleOffice4Me/not-in-manifest.txt", b"surprise")
                add_manifest(archive, files)

            with self.assertRaisesRegex(ValueError, "unmanifested"):
                verify_backup(backup)

    def test_verification_rejects_duplicate_archive_members(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = Path(temp) / "duplicate.tar.gz"
            content = b"same name twice"
            files = [
                {"path": "duplicate.txt", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            ]
            with tarfile.open(backup, "w:gz") as archive:
                add_bytes(archive, "SimpleOffice4Me/duplicate.txt", content)
                add_bytes(archive, "SimpleOffice4Me/duplicate.txt", content)
                add_manifest(archive, files)

            with self.assertRaisesRegex(ValueError, "duplicate members"):
                verify_backup(backup)

    def test_verification_rejects_links_and_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = Path(temp) / "link.tar.gz"
            with tarfile.open(backup, "w:gz") as archive:
                link = tarfile.TarInfo("SimpleOffice4Me/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../outside"
                archive.addfile(link)
                add_manifest(archive, [])

            with self.assertRaisesRegex(ValueError, "forbidden type"):
                verify_backup(backup)
