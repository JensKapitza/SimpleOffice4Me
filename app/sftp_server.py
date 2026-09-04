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

from .attachment_security import AttachmentSecurity
from .ssh_keys import authenticate_key
from .virtual_filesystem import VirtualFileSystem

try:  # Optional dependency; the web application does not require Paramiko.
    import paramiko
except ImportError:  # pragma: no cover - exercised by installation checks
    paramiko = None


def _require_paramiko():
    if paramiko is None:
        raise RuntimeError(
            "SFTP-Abhängigkeit Paramiko fehlt. Bitte den plattformspezifischen "
            "SFTP-Starter verwenden: ./start-sftp.sh init (Windows: start-sftp.bat init)."
        )
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


def _bounded_environment_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _password_authentication_enabled() -> bool:
    return os.environ.get("SIMPLEOFFICE_SFTP_PASSWORD_AUTH", "true").strip().lower() in {"1", "true", "yes", "on"}


if paramiko is not None:
    class _BufferedWriteHandle(paramiko.SFTPHandle):
        def __init__(self, vfs: VirtualFileSystem, actor: str, path: str, flags: int, app=None):
            super().__init__(flags)
            self.vfs, self.actor, self.path = vfs, actor, path
            self.buffer = io.BytesIO()
            self.expected_sha256 = ""
            self.app = app
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
                content = self.buffer.getvalue()
                if self.app is not None and self.app.config.get("WEBDAV_UPLOAD_SCAN", False):
                    scan = AttachmentSecurity(self.vfs.root).scan_webdav_upload(
                        content, self.actor, self.path,
                        int(self.app.config.get("WEBDAV_QUARANTINE_BYTES", 1024 * 1024 * 1024)),
                        source_type="sftp-write",
                    )
                    if scan.get("verdict") != "clean":
                        raise PermissionError("malware scan rejected the SFTP upload")
                self.vfs.write_bytes(
                    self.actor, self.path, content,
                    expected_sha256=self.expected_sha256,
                    max_bytes=_bounded_environment_integer(
                        "SIMPLEOFFICE_SFTP_MAX_BYTES", 512 * 1024 * 1024, 1, 8 * 1024 * 1024 * 1024,
                    ),
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
            self.app = getattr(server, "app", None)

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
                    return _BufferedWriteHandle(self.vfs, self.actor, path, flags, self.app)
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

        def posix_rename(self, oldpath, newpath):
            if self.scope != "write":
                return paramiko.SFTP_PERMISSION_DENIED
            try:
                self.vfs.rename(self.actor, oldpath, newpath, replace=True)
                return paramiko.SFTP_OK
            except (OSError, RuntimeError, ValueError) as exc:
                return _sftp_status(exc)

        def chattr(self, path, attr):
            """Support the SFTP v3 size attribute used by truncate(2)."""
            if self.scope != "write":
                return paramiko.SFTP_PERMISSION_DENIED
            if getattr(attr, "st_size", None) is None:
                atime = getattr(attr, "st_atime", None)
                mtime = getattr(attr, "st_mtime", None)
                if atime is None and mtime is None:
                    return paramiko.SFTP_OK
                try:
                    self.vfs.set_times(self.actor, path, atime=atime, mtime=mtime)
                    return paramiko.SFTP_OK
                except (OSError, RuntimeError, ValueError) as exc:
                    return _sftp_status(exc)
            try:
                size = int(attr.st_size)
                maximum = _bounded_environment_integer(
                    "SIMPLEOFFICE_SFTP_MAX_BYTES", 512 * 1024 * 1024, 1, 8 * 1024 * 1024 * 1024,
                )
                if size < 0 or size > maximum:
                    raise ValueError("invalid file size")
                content = self.vfs.read_bytes(self.actor, path)
                resized = content[:size] if size <= len(content) else content + b"\0" * (size - len(content))
                self.vfs.write_bytes(self.actor, path, resized, max_bytes=maximum)
                return paramiko.SFTP_OK
            except (OSError, RuntimeError, ValueError) as exc:
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
            self.failed_attempts = 0

        def _failed(self):
            self.failed_attempts += 1
            return paramiko.AUTH_FAILED

        def _accept(self, username, identity):
            self.username, self.identity = username, identity
            self.vfs = VirtualFileSystem.from_environment(self.app.config["DOCUMENT_ROOT"])
            return paramiko.AUTH_SUCCESSFUL

        def check_auth_password(self, username, password):
            if not _password_authentication_enabled() or self.failed_attempts >= 5:
                return self._failed()
            from .webdav import authenticate_password
            with self.app.app_context():
                identity = authenticate_password(username, password, record_use=True)
                if identity is None:
                    return self._failed()
                return self._accept(username, identity)

        def check_auth_publickey(self, username, key):
            if self.failed_attempts >= 5:
                return self._failed()
            with self.app.app_context():
                identity = authenticate_key(
                    self.app.config["DOCUMENT_ROOT"], username, key.get_name(), key.asbytes(),
                )
                return self._accept(username, identity) if identity is not None else self._failed()

        def get_allowed_auths(self, username):
            return "publickey,password" if _password_authentication_enabled() else "publickey"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_shell_request(self, channel):
            return False

        def check_channel_exec_request(self, channel, command):
            if os.environ.get("SIMPLEOFFICE_RSYNC_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
                return False
            try:
                from .rsync_server import RestrictedRsyncSession, parse_rsync_command
                request = parse_rsync_command(command)
                target = self.vfs.resolve(request.virtual_path)
                if target.exists():
                    self.vfs.require(f"rsync:{self.username}", target, "read")
                elif request.sender:
                    return False
                else:
                    self.vfs.require(f"rsync:{self.username}", target.parent, "write")
                if not request.sender and self.identity.get("scope") != "write":
                    return False
                RestrictedRsyncSession(self, channel, request).start()
                return True
            except (OSError, RuntimeError, UnicodeError, ValueError):
                return False

        def check_channel_forward_agent_request(self, channel):
            return False

        def check_port_forward_request(self, address, port):
            return False


def _serve_client(client: socket.socket, host_key, app) -> None:
    library = _require_paramiko()
    transport = library.Transport(client, disabled_algorithms={
        "kex": ["diffie-hellman-group1-sha1", "diffie-hellman-group14-sha1"],
        "macs": ["hmac-md5", "hmac-md5-96", "hmac-sha1-96"],
        "pubkeys": ["ssh-rsa"],
    })
    transport.banner_timeout = 15
    transport.auth_timeout = 30
    transport.channel_timeout = 30
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
    port = _bounded_environment_integer("SIMPLEOFFICE_SFTP_PORT", 2222, 1, 65535)
    from . import app
    max_clients = _bounded_environment_integer("SIMPLEOFFICE_SFTP_MAX_CLIENTS", 32, 1, 512)
    capacity = threading.BoundedSemaphore(max_clients)
    listener = socket.create_server((host, port), backlog=max_clients)
    try:
        while True:
            client, _address = listener.accept()
            if not capacity.acquire(blocking=False):
                client.close()
                continue
            client.settimeout(30)
            def run_client(connection=client):
                try:
                    _serve_client(connection, host_key, app)
                finally:
                    capacity.release()
            threading.Thread(target=run_client, daemon=True).start()
    finally:
        listener.close()


if __name__ == "__main__":
    serve()
