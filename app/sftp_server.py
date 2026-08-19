"""Optional, shell-free SFTP service for the SimpleOffice virtual filesystem."""

from __future__ import annotations

import errno
import hashlib
import io
import os
import socket
import stat
import threading
from pathlib import Path

from .virtual_filesystem import VirtualFileSystem

try:  # Optional dependency; the web application does not require Paramiko.
    import paramiko
except ImportError:  # pragma: no cover - exercised by installation checks
    paramiko = None


def _require_paramiko():
    if paramiko is None:
        raise RuntimeError("SFTP requires: pip install '.[sftp]'")
    return paramiko


def _sftp_status(exc: BaseException) -> int:
    library = _require_paramiko()
    if isinstance(exc, PermissionError):
        return library.SFTP_PERMISSION_DENIED
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return library.SFTP_NO_SUCH_FILE
    if isinstance(exc, FileExistsError):
        return library.SFTP_FAILURE
    return library.SFTP_FAILURE


if paramiko is not None:
    class _BufferedWriteHandle(paramiko.SFTPHandle):
        def __init__(self, vfs: VirtualFileSystem, actor: str, path: str, flags: int):
            super().__init__(flags)
            self.vfs, self.actor, self.path = vfs, actor, path
            self.buffer = io.BytesIO()
            self.expected_sha256 = ""
            resource = vfs.resolve(path)
            if resource.is_file() and not resource.is_symlink():
                original = vfs.read_bytes(actor, path)
                self.expected_sha256 = hashlib.sha256(original).hexdigest()
                if not flags & os.O_TRUNC:
                    self.buffer.write(original)
            if flags & os.O_APPEND:
                self.buffer.seek(0, io.SEEK_END)
            else:
                self.buffer.seek(0)

        def write(self, offset: int, data: bytes):
            try:
                self.buffer.seek(offset)
                self.buffer.write(data)
                return paramiko.SFTP_OK
            except (OSError, ValueError) as exc:
                return _sftp_status(exc)

        def read(self, offset: int, length: int):
            self.buffer.seek(offset)
            return self.buffer.read(length)

        def close(self):
            try:
                self.vfs.write_bytes(
                    self.actor, self.path, self.buffer.getvalue(),
                    expected_sha256=self.expected_sha256,
                    max_bytes=int(os.environ.get("SIMPLEOFFICE_SFTP_MAX_BYTES", 512 * 1024 * 1024)),
                )
                return paramiko.SFTP_OK
            except (OSError, RuntimeError, ValueError) as exc:
                return _sftp_status(exc)
            finally:
                self.buffer.close()


    class RestrictedSFTP(paramiko.SFTPServerInterface):
        """Paramiko adapter with no direct physical-path or shell access."""

        def __init__(self, server, *args, **kwargs):
            super().__init__(server, *args, **kwargs)
            self.vfs = server.vfs
            self.actor = f"sftp:{server.username}"
            self.scope = server.identity.get("scope", "read")

        @staticmethod
        def _attributes(path: Path):
            attributes = paramiko.SFTPAttributes.from_stat(path.stat(follow_symlinks=False))
            attributes.filename = path.name
            return attributes

        def canonicalize(self, path):
            try:
                relative = self.vfs.relative(self.vfs.resolve(path))
                return "/" if relative == "." else "/" + relative
            except ValueError:
                return "/"

        def list_folder(self, path):
            try:
                return [self._attributes(self.vfs.resolve(item.path)) for item in self.vfs.entries(self.actor, path)]
            except (OSError, ValueError) as exc:
                return _sftp_status(exc)

        def stat(self, path):
            try:
                resource = self.vfs.require(self.actor, path, "read")
                return self._attributes(resource)
            except (OSError, ValueError) as exc:
                return _sftp_status(exc)

        lstat = stat

        def open(self, path, flags, attr):
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
            if writing:
                if self.scope != "write":
                    return paramiko.SFTP_PERMISSION_DENIED
                try:
                    resource = self.vfs.resolve(path)
                    self.vfs.require(
                        self.actor, resource if resource.exists() else resource.parent, "write",
                    )
                    return _BufferedWriteHandle(self.vfs, self.actor, path, flags)
                except (OSError, ValueError) as exc:
                    return _sftp_status(exc)
            try:
                handle = paramiko.SFTPHandle(flags)
                handle.readfile = io.BytesIO(self.vfs.read_bytes(self.actor, path))
                return handle
            except (OSError, ValueError) as exc:
                return _sftp_status(exc)

        def mkdir(self, path, attr):
            if self.scope != "write":
                return paramiko.SFTP_PERMISSION_DENIED
            try:
                self.vfs.mkdir(self.actor, path)
                return paramiko.SFTP_OK
            except (OSError, ValueError) as exc:
                return _sftp_status(exc)

        def remove(self, path):
            return self._remove(path)

        def rmdir(self, path):
            return self._remove(path)

        def _remove(self, path):
            if self.scope != "write":
                return paramiko.SFTP_PERMISSION_DENIED
            try:
                self.vfs.remove(self.actor, path)
                return paramiko.SFTP_OK
            except (OSError, ValueError) as exc:
                return _sftp_status(exc)

        def rename(self, oldpath, newpath):
            if self.scope != "write":
                return paramiko.SFTP_PERMISSION_DENIED
            try:
                self.vfs.rename(self.actor, oldpath, newpath)
                return paramiko.SFTP_OK
            except (OSError, ValueError) as exc:
                return _sftp_status(exc)

        def symlink(self, target_path, path):
            return paramiko.SFTP_OP_UNSUPPORTED

        def chmod(self, path, mode):
            return paramiko.SFTP_OP_UNSUPPORTED


    class _AuthenticationServer(paramiko.ServerInterface):
        def __init__(self, app):
            self.app = app
            self.username = ""
            self.identity = None
            self.vfs = None

        def check_auth_password(self, username, password):
            from .webdav import authenticate_password
            with self.app.app_context():
                identity = authenticate_password(username, password, record_use=True)
                if identity is None:
                    return paramiko.AUTH_FAILED
                self.username, self.identity = username, identity
                self.vfs = VirtualFileSystem.from_environment(self.app.config["DOCUMENT_ROOT"])
                return paramiko.AUTH_SUCCESSFUL

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


def _serve_client(client: socket.socket, host_key, app) -> None:
    library = _require_paramiko()
    transport = library.Transport(client)
    transport.add_server_key(host_key)
    transport.set_subsystem_handler("sftp", library.SFTPServer, RestrictedSFTP)
    server = _AuthenticationServer(app)
    try:
        transport.start_server(server=server)
        while transport.is_active():
            transport.join(1)
    finally:
        transport.close()
        client.close()


def serve() -> None:
    """Run the explicitly configured SFTP-only service."""
    library = _require_paramiko()
    key_path = Path(os.environ.get("SIMPLEOFFICE_SFTP_HOST_KEY", "")).expanduser()
    if not key_path.is_file() or key_path.is_symlink():
        raise RuntimeError("SIMPLEOFFICE_SFTP_HOST_KEY must name a protected Ed25519/RSA private key")
    if os.name != "nt" and stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        raise RuntimeError("SFTP host key permissions are too broad; use chmod 600")
    host_key = library.PKey.from_path(str(key_path))
    host = os.environ.get("SIMPLEOFFICE_SFTP_BIND", "127.0.0.1")
    port = int(os.environ.get("SIMPLEOFFICE_SFTP_PORT", "2222"))
    from . import app
    listener = socket.create_server((host, port), backlog=20)
    try:
        while True:
            client, _address = listener.accept()
            client.settimeout(30)
            threading.Thread(target=_serve_client, args=(client, host_key, app), daemon=True).start()
    finally:
        listener.close()


if __name__ == "__main__":
    serve()
