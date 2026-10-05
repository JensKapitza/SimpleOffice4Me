"""Guarded uploads must not recreate a file deleted after it was opened."""
import hashlib
import os
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from app import sftp_server
import test_sftp_server as _fixture


@unittest.skipUnless(_fixture.paramiko is not None, 'install the optional sftp extra')
class DeletedUploadTests(unittest.TestCase):
    setUp = _fixture.SftpVirtualFilesystemTest.setUp
    tearDown = _fixture.SftpVirtualFilesystemTest.tearDown

    def test_deleted_file_is_not_recreated_by_open_upload(self):
        for flags in (os.O_WRONLY | os.O_TRUNC, os.O_WRONLY | os.O_APPEND, os.O_RDWR):
            with self.subTest(flags=flags):
                path = '/shared/upload.txt'
                self.vfs.write_bytes('sftp:editor', path, b'original')
                handle = sftp_server._BufferedWriteHandle(self.vfs, 'sftp:editor', path, flags)
                self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(0, b'stale'))
                self.vfs.remove('sftp:editor', path)
                self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.close())
                self.assertTrue(handle.buffer.closed)
                self.assertFalse((self.root / 'shared/upload.txt').exists())

    def test_missing_guarded_target_never_reaches_storage_create(self):
        self.vfs.remove('sftp:editor', '/shared/notes.txt')
        with patch.object(self.vfs, '_storage') as storage:
            with self.assertRaisesRegex(ValueError, 'changed since'):
                self.vfs.write_bytes('sftp:editor', '/shared/notes.txt', b'stale',
                                     expected_sha256=hashlib.sha256(b'one').hexdigest())
            storage.return_value.create_bytes.assert_not_called()
        self.assertFalse((self.root / 'shared/notes.txt').exists())

    def test_guarded_missing_target_still_requires_write_permission(self):
        with self.assertRaises(PermissionError):
            self.vfs.write_bytes('sftp:reader', '/shared/new.txt', b'denied',
                                 expected_sha256=hashlib.sha256(b'one').hexdigest())
        self.assertFalse((self.root / 'shared/new.txt').exists())

    def test_new_upload_and_existing_guarded_replacement_still_work(self):
        self.vfs.write_bytes('sftp:editor', '/shared/new.txt', b'new')
        self.vfs.write_bytes('sftp:editor', '/shared/notes.txt', b'updated',
                             expected_sha256=hashlib.sha256(b'one').hexdigest())
        self.assertEqual(b'new', (self.root / 'shared/new.txt').read_bytes())
        self.assertEqual(b'updated', (self.root / 'shared/notes.txt').read_bytes())

    def test_close_status_is_forwarded_and_consumed_handles_are_rejected(self):
        from paramiko.sftp import CMD_CLOSE
        for status in (_fixture.paramiko.SFTP_OK, _fixture.paramiko.SFTP_FAILURE,
                       _fixture.paramiko.SFTP_PERMISSION_DENIED, None):
            server = object.__new__(sftp_server._StatusSFTPServer)
            handle = Mock()
            handle.close.return_value = status
            server.file_table = {b'file': handle}
            server.folder_table = {b'folder': object()}
            server._send_status = Mock()
            def close(name):
                message = _fixture.paramiko.Message()
                message.add_string(name)
                server._process(CMD_CLOSE, 1, _fixture.paramiko.Message(message.asbytes()))
            close(b'file')
            server._send_status.assert_called_with(1, _fixture.paramiko.SFTP_OK if status is None else status)
            handle.close.assert_called_once_with()
            self.assertEqual({}, server.file_table)
            close(b'folder')
            server._send_status.assert_called_with(1, _fixture.paramiko.SFTP_OK)
            self.assertEqual({}, server.folder_table)
            close(b'file')
            server._send_status.assert_called_with(1, _fixture.paramiko.SFTP_BAD_MESSAGE, 'Invalid handle')

    def test_real_transport_close_rejects_deleted_target_and_keeps_session(self):
        from paramiko.sftp import CMD_CLOSE
        application = Flask(__name__)
        application.config['DOCUMENT_ROOT'] = str(self.root)
        server_socket, client_socket = socket.socketpair()
        key = _fixture.paramiko.RSAKey.generate(2048)
        errors = []
        def serve():
            try:
                sftp_server._serve_client(server_socket, key, application)
            except (EOFError, OSError, _fixture.paramiko.SSHException) as exc:
                errors.append(type(exc).__name__)
        with patch('app.webdav.authenticate_password', return_value={'username': 'editor', 'scope': 'write'}):
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            transport = _fixture.paramiko.Transport(client_socket)
            transport.banner_timeout = 5
            transport.auth_timeout = 5
            try:
                transport.connect(username='editor', password='test-app-password')
                client = _fixture.paramiko.SFTPClient.from_transport(transport)
                client.get_channel().settimeout(5)
                handle = client.open('/shared/notes.txt', 'wb', bufsize=0)
                handle.write(b'stale')
                client.remove('/shared/notes.txt')
                # SFTPFile.close suppresses IOError; inspect the protocol status.
                with self.assertRaises(OSError):
                    client._request(CMD_CLOSE, handle.handle)
                handle.close()
                self.assertFalse((self.root / 'shared/notes.txt').exists())
                with client.open('/shared/new.txt', 'wb', bufsize=0) as fresh:
                    fresh.write(b'fresh')
                self.assertEqual(b'fresh', (self.root / 'shared/new.txt').read_bytes())
                client.close()
            finally:
                transport.close()
                thread.join(timeout=5)
                server_socket.close()
                client_socket.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
