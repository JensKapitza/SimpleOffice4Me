import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import paramiko

from app.document_store import DocumentStore
from app.sftp_server import RestrictedSFTP, _BufferedWriteHandle
from app.virtual_filesystem import VirtualFileSystem


class SftpVirtualFilesystemTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "shared").mkdir()
        (self.root / "shared" / "notes.txt").write_bytes(b"one")
        DocumentStore(self.root).scan()
        self.vfs = VirtualFileSystem(self.root, {"admin"})
        self.vfs.set_grants(".", {"reader": "read", "editor": "write"}, "admin")

    def tearDown(self):
        self.temp.cleanup()

    def adapter(self, username, scope="write"):
        server = SimpleNamespace(
            vfs=self.vfs, username=username, identity={"scope": scope},
        )
        return RestrictedSFTP(server)

    def test_read_role_can_list_and_read_but_not_open_for_writing(self):
        adapter = self.adapter("reader")
        self.assertEqual(["shared"], [item.filename for item in adapter.list_folder("/")])
        handle = adapter.open("/shared/notes.txt", os.O_RDONLY, None)
        self.assertEqual(b"one", handle.read(0, 20))
        self.assertEqual(
            paramiko.SFTP_PERMISSION_DENIED,
            adapter.open("/shared/notes.txt", os.O_WRONLY | os.O_TRUNC, None),
        )

    def test_editor_write_is_versioned_and_guarded_against_lost_updates(self):
        first = _BufferedWriteHandle(
            self.vfs, "sftp:editor", "/shared/notes.txt", os.O_WRONLY | os.O_TRUNC,
        )
        second = _BufferedWriteHandle(
            self.vfs, "sftp:editor", "/shared/notes.txt", os.O_WRONLY | os.O_TRUNC,
        )
        self.assertEqual(paramiko.SFTP_OK, first.write(0, b"two"))
        self.assertEqual(paramiko.SFTP_OK, first.close())
        self.assertEqual(paramiko.SFTP_OK, second.write(0, b"stale"))
        self.assertEqual(paramiko.SFTP_FAILURE, second.close())
        self.assertEqual(b"two", (self.root / "shared" / "notes.txt").read_bytes())


if __name__ == "__main__":
    unittest.main()
