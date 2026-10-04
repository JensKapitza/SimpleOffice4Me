"""SFTP REMOVE and RMDIR must preserve objects of the wrong type."""
import socket
import threading
import unittest
from unittest.mock import patch

from flask import Flask
from app import sftp_server
import test_sftp_server as _fixture


@unittest.skipUnless(_fixture.paramiko is not None, 'install the optional sftp extra')
class SftpRemoveTests(unittest.TestCase):
    setUp = _fixture.SftpVirtualFilesystemTest.setUp
    tearDown = _fixture.SftpVirtualFilesystemTest.tearDown
    adapter = _fixture.SftpVirtualFilesystemTest.adapter

    def test_rmdir_rejects_file_without_deleting_it(self):
        self.assertNotEqual(_fixture.paramiko.SFTP_OK, self.adapter('editor').rmdir('/shared/notes.txt'))
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_remove_rejects_empty_directory_without_deleting_it(self):
        (self.root / 'shared/empty').mkdir()
        self.assertNotEqual(_fixture.paramiko.SFTP_OK, self.adapter('editor').remove('/shared/empty'))
        self.assertTrue((self.root / 'shared/empty').is_dir())

    def test_valid_remove_and_rmdir_still_work(self):
        adapter = self.adapter('editor')
        self.assertEqual(_fixture.paramiko.SFTP_OK, adapter.remove('/shared/notes.txt'))
        self.assertFalse((self.root / 'shared/notes.txt').exists())
        self.assertEqual(_fixture.paramiko.SFTP_OK, adapter.rmdir('/shared'))
        self.assertFalse((self.root / 'shared').exists())

    def test_nonempty_directory_is_preserved(self):
        self.assertNotEqual(_fixture.paramiko.SFTP_OK, self.adapter('editor').rmdir('/shared'))
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())

    def test_read_scope_and_acl_cannot_delete_either_type(self):
        (self.root / 'shared/empty').mkdir()
        for adapter in (self.adapter('reader'), self.adapter('editor', scope='read')):
            self.assertEqual(_fixture.paramiko.SFTP_PERMISSION_DENIED, adapter.remove('/shared/notes.txt'))
            self.assertEqual(_fixture.paramiko.SFTP_PERMISSION_DENIED, adapter.rmdir('/shared/empty'))
        self.assertEqual(b'one', (self.root / 'shared/notes.txt').read_bytes())
        self.assertTrue((self.root / 'shared/empty').is_dir())

    def test_vfs_default_removal_keeps_shared_protocol_behavior(self):
        (self.root / 'shared/empty').mkdir()
        self.vfs.remove('sftp:editor', '/shared/empty')
        self.vfs.remove('sftp:editor', '/shared/notes.txt')
        self.assertFalse((self.root / 'shared/empty').exists())
        self.assertFalse((self.root / 'shared/notes.txt').exists())

    def test_real_transport_rejects_wrong_type_and_remains_usable(self):
        application = Flask(__name__)
        application.config['DOCUMENT_ROOT'] = str(self.root)
        (self.root / 'shared/empty').mkdir()
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
                with self.assertRaises(OSError):
                    client.rmdir('/shared/notes.txt')
                with self.assertRaises(OSError):
                    client.remove('/shared/empty')
                with client.open('/shared/notes.txt', 'rb') as handle:
                    self.assertEqual(b'one', handle.read())
                client.rmdir('/shared/empty')
                client.remove('/shared/notes.txt')
                client.close()
            finally:
                transport.close()
                thread.join(timeout=5)
                server_socket.close()
                client_socket.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
