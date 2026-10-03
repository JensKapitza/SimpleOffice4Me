"""Reject oversized SFTP writes before allocation and preserve original files."""
import os
import socket
import threading
import unittest
from unittest.mock import patch

from flask import Flask
from app import sftp_server
import test_sftp_server as _fixture


@unittest.skipUnless(_fixture.paramiko is not None, 'install the optional sftp extra to test uploads')
class SftpUploadLimitTests(unittest.TestCase):
    setUp = _fixture.SftpVirtualFilesystemTest.setUp
    tearDown = _fixture.SftpVirtualFilesystemTest.tearDown

    def handle(self, path='/shared/notes.txt'):
        with patch.dict(os.environ, {'SIMPLEOFFICE_SFTP_MAX_BYTES': '8'}):
            return sftp_server._BufferedWriteHandle(self.vfs, 'sftp:editor', path, os.O_WRONLY | os.O_TRUNC)

    def test_exact_limit_and_sparse_writes_are_committed(self):
        handle = self.handle()
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(0, b'abc'))
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(7, b'z'))
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.close())
        self.assertEqual(b'abc\0\0\0\0z', (self.root / 'shared/notes.txt').read_bytes())

    def test_oversized_chunk_aborts_entire_upload_and_rejects_later_writes(self):
        handle = self.handle()
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(0, b'abc'))
        self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.write(3, b'123456'))
        self.assertEqual(b'abc', handle.buffer.getvalue())
        self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.write(0, b'valid'))
        with patch.object(self.vfs, 'write_bytes') as commit:
            self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.close())
        commit.assert_not_called()
        self.assertTrue(handle.buffer.closed)
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_huge_or_negative_offsets_are_rejected_before_seeking(self):
        for offset, data in ((2**40, b'x'), (2**40, b''), (-1, b'x'), (8, b'x')):
            with self.subTest(offset=offset, data=data):
                handle = self.handle('/shared/new.txt')
                with patch.object(handle, 'buffer', wraps=handle.buffer) as buffer:
                    buffer.seek.side_effect = AssertionError('oversized write reached the buffer')
                    self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.write(offset, data))
                    buffer.seek.assert_not_called()
                    buffer.write.assert_not_called()
                    self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.close())
                self.assertFalse((self.root / 'shared/new.txt').exists())

    def test_buffer_io_failure_cannot_commit_partial_content(self):
        handle = self.handle()
        with patch.object(handle, 'buffer', wraps=handle.buffer) as buffer:
            buffer.write.side_effect = OSError('buffer write failed')
            self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.write(0, b'abc'))
            self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.close())
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_limit_is_fixed_for_lifetime_of_open_handle(self):
        handle = self.handle()
        with patch.dict(os.environ, {'SIMPLEOFFICE_SFTP_MAX_BYTES': '100'}):
            self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.write(0, b'123456789'))
            self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.close())
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_real_transport_rejects_oversized_offset_and_preserves_original(self):
        application = Flask(__name__)
        application.config['DOCUMENT_ROOT'] = str(self.root)
        server_socket, client_socket = socket.socketpair()
        host_key = _fixture.paramiko.RSAKey.generate(2048)
        errors = []
        def serve():
            try:
                sftp_server._serve_client(server_socket, host_key, application)
            except (EOFError, OSError, _fixture.paramiko.SSHException) as exc:
                errors.append(type(exc).__name__)
        with patch.dict(os.environ, {'SIMPLEOFFICE_SFTP_MAX_BYTES': '8'}), patch('app.webdav.authenticate_password', return_value={'username': 'editor', 'scope': 'write'}):
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
                handle.seek(1024)
                with self.assertRaises(OSError):
                    handle.write(b'x')
                # Paramiko suppresses CLOSE errors after a failed write; the
                # server must still discard the upload and retain the original.
                handle.close()
                self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())
                client.close()
            finally:
                transport.close()
                thread.join(timeout=5)
                server_socket.close()
                client_socket.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
