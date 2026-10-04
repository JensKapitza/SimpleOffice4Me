"""Unsigned SFTP read offsets must not overflow BytesIO or break a session."""
import os
import socket
import threading
import unittest
from unittest.mock import patch

from flask import Flask
from app import sftp_server
import test_sftp_server as _fixture


@unittest.skipUnless(_fixture.paramiko is not None, 'install the optional sftp extra')
class SftpReadRangeTests(unittest.TestCase):
    setUp = _fixture.SftpVirtualFilesystemTest.setUp
    tearDown = _fixture.SftpVirtualFilesystemTest.tearDown
    adapter = _fixture.SftpVirtualFilesystemTest.adapter

    def test_uint64_offsets_return_eof_and_allow_subsequent_reads(self):
        for flags in (os.O_RDONLY, os.O_RDWR):
            handle = self.adapter('editor').open('/shared/notes.txt', flags, None)
            try:
                for offset in (3, 4, 2**63, 2**64 - 1):
                    with self.subTest(flags=flags, offset=offset):
                        self.assertEqual(b'', handle.read(offset, 1))
                self.assertEqual(b'one', handle.read(0, 3))
            finally:
                handle.close()

    def test_ranges_are_bounded_by_file_size_and_preserve_position(self):
        handle = self.adapter('editor').open('/shared/notes.txt', os.O_RDWR, None)
        try:
            handle.buffer.seek(1)
            self.assertEqual(b'ne', handle.read(1, 2**32 - 1))
            self.assertEqual(1, handle.buffer.tell())
            self.assertEqual(b'', handle.read(0, 0))
            self.assertEqual(b'e', handle.read(2, 2))
            self.assertEqual(b'o', handle.read(0, 1))
            self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(3, b'x'))
        finally:
            self.assertEqual(_fixture.paramiko.SFTP_OK, handle.close())
        self.assertEqual(b'onex', (self.root / 'shared/notes.txt').read_bytes())

    def test_invalid_ranges_are_status_errors_without_poisoning_handle(self):
        handle = self.adapter('reader').open('/shared/notes.txt', os.O_RDONLY, None)
        try:
            self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.read(-1, 1))
            self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.read(0, -1))
            self.assertEqual(b'one', handle.read(0, 3))
        finally:
            handle.close()
        self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.read(0, 1))

    def test_read_only_close_releases_buffer_without_writing(self):
        handle = self.adapter('reader').open('/shared/notes.txt', os.O_RDONLY, None)
        with patch.object(self.vfs, 'write_bytes', side_effect=AssertionError('read close must not write')):
            self.assertEqual(_fixture.paramiko.SFTP_OK, handle.close())
        self.assertTrue(handle.buffer.closed)
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_real_transport_uint64_eof_keeps_session_usable(self):
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
                for mode in ('rb', 'r+'):
                    with client.open('/shared/notes.txt', mode, bufsize=0) as handle:
                        handle.seek(2**64 - 1)
                        self.assertEqual(b'', handle.read(1))
                        handle.seek(0)
                        self.assertEqual(b'one', handle.read(3))
                self.assertEqual(3, client.stat('/shared/notes.txt').st_size)
                client.close()
            finally:
                transport.close()
                thread.join(timeout=5)
                server_socket.close()
                client_socket.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
