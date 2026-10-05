"""New uploads must not overwrite a target created while they were open."""
import os
import unittest
from unittest.mock import patch

from app import sftp_server
from app.safe_paths import resolve_for_write_under
import test_sftp_server as _fixture


@unittest.skipUnless(_fixture.paramiko is not None, 'install the optional sftp extra')
class ConcurrentCreateTests(unittest.TestCase):
    setUp = _fixture.SftpVirtualFilesystemTest.setUp
    tearDown = _fixture.SftpVirtualFilesystemTest.tearDown

    def test_two_new_uploads_preserve_first_completed_content(self):
        for flags in (os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                      os.O_WRONLY | os.O_CREAT | os.O_APPEND, os.O_RDWR | os.O_CREAT):
            with self.subTest(flags=flags):
                path = '/shared/new.txt'
                first = sftp_server._BufferedWriteHandle(self.vfs, 'sftp:editor', path, flags)
                second = sftp_server._BufferedWriteHandle(self.vfs, 'sftp:editor', path, flags)
                self.assertEqual(_fixture.paramiko.SFTP_OK, first.write(0, b'first'))
                self.assertEqual(_fixture.paramiko.SFTP_OK, second.write(0, b'second'))
                self.assertEqual(_fixture.paramiko.SFTP_OK, first.close())
                self.assertEqual(_fixture.paramiko.SFTP_FAILURE, second.close())
                self.assertTrue(second.buffer.closed)
                self.assertEqual(b'first', (self.root / 'shared/new.txt').read_bytes())
                self.vfs.remove('sftp:editor', path)

    def test_other_vfs_writer_creating_target_is_preserved(self):
        handle = sftp_server._BufferedWriteHandle(
            self.vfs, 'sftp:editor', '/shared/new.txt', os.O_WRONLY | os.O_CREAT)
        handle.write(0, b'stale')
        self.vfs.write_bytes('webdav:editor', '/shared/new.txt', b'other writer')
        self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.close())
        self.assertEqual(b'other writer', (self.root / 'shared/new.txt').read_bytes())

    def test_create_only_existing_target_never_reaches_storage_replace(self):
        with patch.object(self.vfs, '_storage') as storage:
            with self.assertRaises(FileExistsError):
                self.vfs.write_bytes('sftp:editor', '/shared/notes.txt', b'denied', create_only=True)
            storage.return_value.replace_bytes.assert_not_called()
            storage.return_value.create_bytes.assert_not_called()
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_create_only_still_requires_write_permission(self):
        for path in ('/shared/notes.txt', '/shared/missing.txt'):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                self.vfs.write_bytes('sftp:reader', path, b'denied', create_only=True)
        self.assertFalse((self.root / 'shared/missing.txt').exists())

    def test_create_only_new_target_and_default_replacement_still_work(self):
        self.vfs.write_bytes('sftp:editor', '/shared/new.txt', b'new', create_only=True)
        self.vfs.write_bytes('sftp:editor', '/shared/new.txt', b'updated')
        self.assertEqual(b'updated', (self.root / 'shared/new.txt').read_bytes())

    def test_target_created_after_existence_check_is_preserved(self):
        def concurrent_create(root, relative):
            resource = resolve_for_write_under(root, relative)
            self.vfs.store.create_document_at(str(relative), b'first', 'sftp:editor')
            return resource

        with patch('app.virtual_filesystem.resolve_for_write_under', side_effect=concurrent_create):
            with self.assertRaises(ValueError):
                self.vfs.write_bytes('sftp:editor', '/shared/new.txt', b'second', create_only=True)
        self.assertEqual(b'first', (self.root / 'shared/new.txt').read_bytes())
