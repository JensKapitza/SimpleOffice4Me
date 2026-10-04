"""SFTP append must ignore client offsets without bypassing upload limits."""
import os
import socket
import threading
import unittest
from unittest.mock import patch

from flask import Flask
from app import sftp_server
import test_sftp_server as _fixture


@unittest.skipUnless(_fixture.paramiko is not None, 'install the optional sftp extra to test append')
class SftpAppendTests(unittest.TestCase):
    setUp = _fixture.SftpVirtualFilesystemTest.setUp
    tearDown = _fixture.SftpVirtualFilesystemTest.tearDown

    def handle(self, flags=os.O_WRONLY | os.O_APPEND, limit=8):
        with patch.dict(os.environ, {'SIMPLEOFFICE_SFTP_MAX_BYTES': str(limit)}):
            return sftp_server._BufferedWriteHandle(self.vfs, 'sftp:editor', '/shared/notes.txt', flags)

    def test_append_ignores_client_offset_after_reading(self):
        handle = self.handle()
        self.assertEqual(b'o', handle.read(0, 1))
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(0, b'xy'))
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(1, b'z'))
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.close())
        self.assertEqual(b'onexyz', (self.root / 'shared/notes.txt').read_bytes())

    def test_append_ignores_huge_offset_and_checks_actual_end(self):
        handle = self.handle(limit=4)
        buffer = handle.buffer
        def bounded_seek(offset):
            self.assertLessEqual(offset, 4, 'never allocate at the supplied huge offset')
            return buffer.seek(offset)
        with patch.object(handle, 'buffer', wraps=buffer) as guarded:
            guarded.seek.side_effect = bounded_seek
            self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(2**40, b'x'))
        self.assertEqual(b'onex', handle.buffer.getvalue())
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.close())
        self.assertEqual(b'onex', (self.root / 'shared/notes.txt').read_bytes())

    def test_append_cannot_overwrite_to_bypass_cumulative_limit(self):
        handle = self.handle(limit=5)
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(0, b'xy'))
        self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.write(0, b'z'))
        self.assertEqual(_fixture.paramiko.SFTP_FAILURE, handle.close())
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_non_append_keeps_positional_write_semantics(self):
        handle = self.handle(flags=os.O_WRONLY)
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(1, b'x'))
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.close())
        self.assertEqual(b'oxe', (self.root / 'shared/notes.txt').read_bytes())

    def test_truncate_then_append_starts_from_empty_buffer(self):
        handle = self.handle(flags=os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.write(7, b'new'))
        self.assertEqual(_fixture.paramiko.SFTP_OK, handle.close())
        self.assertEqual(b'new', (self.root / 'shared/notes.txt').read_bytes())

    def test_real_transport_append_after_seek_preserves_existing_bytes(self):
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
                with client.open('/shared/notes.txt', 'a+', bufsize=0) as handle:
                    handle.seek(0)
                    self.assertEqual(b'o', handle.read(1))
                    handle.seek(0)
                    handle.write(b'x')
                with client.open('/shared/notes.txt', 'rb') as handle:
                    self.assertEqual(b'onex', handle.read())
                client.close()
            finally:
                transport.close()
                thread.join(timeout=5)
                server_socket.close()
                client_socket.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
