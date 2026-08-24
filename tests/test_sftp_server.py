import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.document_store import DocumentStore
from app.virtual_filesystem import VirtualFileSystem

try:
    import paramiko
    from app.sftp_server import RestrictedSFTP, _AuthenticationServer, _BufferedWriteHandle
    from app.ssh_keys import add_key
    from app.rsync_server import RestrictedRsyncSession, parse_rsync_command
except ImportError:  # The production SFTP service is an optional installation.
    paramiko = None


@unittest.skipUnless(paramiko is not None, "install the optional sftp extra to test its adapter")
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

    def test_truncate_is_versioned_and_zero_extends(self):
        adapter = self.adapter("editor")
        self.assertEqual(paramiko.SFTP_OK, adapter.chattr("/shared/notes.txt", SimpleNamespace(st_size=5)))
        self.assertEqual(b"one\0\0", (self.root / "shared" / "notes.txt").read_bytes())
        self.assertEqual(paramiko.SFTP_OK, adapter.chattr("/shared/notes.txt", SimpleNamespace(st_size=1)))
        self.assertEqual(b"o", (self.root / "shared" / "notes.txt").read_bytes())

    def test_freefilesync_timestamp_and_atomic_temp_file_sequence(self):
        adapter = self.adapter("editor")
        temporary = "/shared/report.txt.ffs_tmp"
        handle = adapter.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, None)
        self.assertEqual(paramiko.SFTP_OK, handle.write(0, b"office"))
        self.assertEqual(paramiko.SFTP_OK, handle.close())
        timestamp = 1_700_000_000
        self.assertEqual(
            paramiko.SFTP_OK,
            adapter.chattr(temporary, SimpleNamespace(st_size=None, st_atime=timestamp, st_mtime=timestamp)),
        )
        self.assertEqual(paramiko.SFTP_OK, adapter.rename(temporary, "/shared/report.txt"))
        self.assertEqual(b"office", (self.root / "shared" / "report.txt").read_bytes())
        self.assertEqual(timestamp, int((self.root / "shared" / "report.txt").stat().st_mtime))

    def test_goodsync_control_folder_and_replace_sequence(self):
        adapter = self.adapter("editor")
        self.assertEqual(paramiko.SFTP_OK, adapter.mkdir("/shared/_gsdata_", None))
        state = adapter.open(
            "/shared/_gsdata_/job-state.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, None,
        )
        self.assertEqual(paramiko.SFTP_OK, state.write(0, b"state"))
        self.assertEqual(paramiko.SFTP_OK, state.close())
        self.assertEqual(
            paramiko.SFTP_OK,
            adapter.posix_rename("/shared/_gsdata_/job-state.tmp", "/shared/notes.txt"),
        )
        self.assertEqual(b"state", (self.root / "shared" / "notes.txt").read_bytes())

    def test_posix_rename_replaces_destination_and_keeps_it_recoverable(self):
        (self.root / "shared" / "incoming.txt").write_bytes(b"replacement")
        DocumentStore(self.root).scan()
        adapter = self.adapter("editor")
        self.assertEqual(
            paramiko.SFTP_OK,
            adapter.posix_rename("/shared/incoming.txt", "/shared/notes.txt"),
        )
        self.assertEqual(b"replacement", (self.root / "shared" / "notes.txt").read_bytes())
        self.assertFalse((self.root / "shared" / "incoming.txt").exists())

    def test_public_key_authentication_and_shell_requests(self):
        from flask import Flask
        app = Flask(__name__)
        app.config["DOCUMENT_ROOT"] = str(self.root)
        key = paramiko.RSAKey.generate(2048)
        add_key(
            self.root, "editor", f"{key.get_name()} {key.get_base64()}",
            label="Test", scope="write", expires_days=30, actor="editor",
        )
        server = _AuthenticationServer(app)
        self.assertEqual(paramiko.AUTH_SUCCESSFUL, server.check_auth_publickey("editor", key))
        self.assertEqual("publickey", server.get_allowed_auths("editor").split(",")[0])
        self.assertFalse(server.check_channel_shell_request(None))
        self.assertFalse(server.check_channel_exec_request(None, b"id"))
        self.assertFalse(server.check_port_forward_request("127.0.0.1", 80))

    def test_rsync_command_parser_accepts_protocol_but_rejects_shell(self):
        request = parse_rsync_command(b"rsync --server --sender -logDtpre.iLsfxCIvu . /shared/")
        self.assertTrue(request.sender)
        self.assertEqual("/shared", request.virtual_path)
        for command in (
            b"id", b"rsync --server -e sh . /shared", b"rsync --server --daemon . /shared",
            b"rsync --server -r . ../../etc",
        ):
            with self.assertRaises(ValueError):
                parse_rsync_command(command)

    def test_rsync_staging_commit_uses_vfs_versions_and_acl(self):
        from flask import Flask
        app = Flask(__name__)
        app.config.update(DOCUMENT_ROOT=str(self.root), WEBDAV_UPLOAD_SCAN=False)
        server = SimpleNamespace(
            vfs=self.vfs, username="editor", identity={"scope": "write"}, app=app,
        )
        request = parse_rsync_command("rsync --server -logDtpre.iLsfxCIvu --delete . /shared/")
        channel = SimpleNamespace()
        session = RestrictedRsyncSession(server, channel, request)
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            initial = session._materialize("/shared", staging)
            (staging / "notes.txt").write_bytes(b"changed")
            (staging / "new.txt").write_bytes(b"new")
            session._commit(staging, initial)
        self.assertEqual(b"changed", (self.root / "shared" / "notes.txt").read_bytes())
        self.assertEqual(b"new", (self.root / "shared" / "new.txt").read_bytes())
        versions = DocumentStore(self.root).content_recovery_versions(
            DocumentStore(self.root).get_document(self.root / "shared" / "notes.txt")["document_id"]
        )
        self.assertTrue(versions)

    def test_rsync_receiver_is_denied_for_read_only_key(self):
        from flask import Flask
        app = Flask(__name__)
        app.config["DOCUMENT_ROOT"] = str(self.root)
        server = _AuthenticationServer(app)
        server.username = "reader"
        server.identity = {"scope": "read"}
        server.vfs = self.vfs
        with unittest.mock.patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_ENABLED": "true"}):
            self.assertFalse(server.check_channel_exec_request(
                SimpleNamespace(), b"rsync --server -logDtpre.iLsfxCIvu . /shared/",
            ))


if __name__ == "__main__":
    unittest.main()
