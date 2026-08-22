"""Restricted rsync-over-SSH bridge for the permission-aware VFS.

The native rsync process never sees DOCUMENT_ROOT.  It operates on a private
staging directory; accepted changes are committed through VirtualFileSystem so
ACLs, history, recovery and conflict checks remain authoritative.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .attachment_security import AttachmentSecurity


_SHORT_OPTIONS = set("vlogDtpre.iLsfxCIvuHAXSWRKNEOJPBbdhkmnqcF0Tz")
_LONG_OPTIONS = {
    "--server", "--sender", "--delete", "--delete-before", "--delete-during",
    "--delete-delay", "--delete-after", "--force", "--ignore-errors",
    "--numeric-ids", "--no-implied-dirs", "--relative", "--recursive",
    "--dirs", "--links", "--times", "--omit-dir-times", "--perms",
    "--owner", "--group", "--devices", "--specials", "--hard-links",
    "--acls", "--xattrs", "--checksum", "--compress", "--whole-file",
    "--inplace", "--partial", "--blocking-io", "--protect-args",
}


@dataclass(frozen=True)
class RsyncRequest:
    sender: bool
    virtual_path: str
    arguments: tuple[str, ...]


def parse_rsync_command(command: bytes | str) -> RsyncRequest:
    """Accept only the command shape emitted by an rsync SSH client."""
    text = command.decode("utf-8", "strict") if isinstance(command, bytes) else command
    parts = shlex.split(text, posix=True)
    if len(parts) < 4 or parts[0] != "rsync" or parts[1] != "--server":
        raise ValueError("only rsync --server is available")
    if parts[-2] != ".":
        raise ValueError("unsupported rsync command layout")
    options = parts[1:-2]
    for option in options:
        if option.startswith("--"):
            name = option.split("=", 1)[0]
            if name not in _LONG_OPTIONS or "=" in option:
                raise ValueError(f"unsupported rsync option: {name}")
        elif option.startswith("-") and option != "-":
            if not set(option[1:]) <= _SHORT_OPTIONS:
                raise ValueError("unsupported rsync short option")
        else:
            raise ValueError("unexpected rsync argument")
    raw_path = parts[-1].replace("\\", "/")
    if "\x00" in raw_path or any(part == ".." for part in Path(raw_path).parts):
        raise ValueError("rsync path escapes the virtual filesystem")
    virtual_path = "/" + raw_path.strip("/")
    return RsyncRequest("--sender" in options, virtual_path or "/", tuple(options))


class RestrictedRsyncSession:
    def __init__(self, server, channel, request: RsyncRequest):
        self.server, self.channel, self.request = server, channel, request
        self.vfs = server.vfs
        self.actor = f"rsync:{server.username}"
        self.scope = server.identity.get("scope", "read")
        self.app = server.app
        self.max_bytes = int(os.environ.get("SIMPLEOFFICE_RSYNC_MAX_BYTES", str(2 * 1024**3)))
        self.max_files = int(os.environ.get("SIMPLEOFFICE_RSYNC_MAX_FILES", "100000"))
        self.timeout = int(os.environ.get("SIMPLEOFFICE_RSYNC_TIMEOUT", "3600"))
        self.initial_directories: set[str] = set()

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True).start()

    def _materialize(self, virtual_root: str, staging: Path) -> dict[str, str]:
        root = self.vfs.resolve(virtual_root)
        if not root.exists():
            if self.request.sender:
                raise FileNotFoundError(virtual_root)
            self.vfs.require(self.actor, root.parent, "write")
            return {}
        root = self.vfs.require(self.actor, root, "read")
        if not root.is_dir() or root.is_symlink():
            raise NotADirectoryError("rsync roots must be virtual directories")
        initial: dict[str, str] = {}
        total = 0
        targets = []
        pending = [(virtual_root, Path("."))]
        while pending:
            current, relative = pending.pop()
            (staging / relative).mkdir(parents=True, exist_ok=True)
            if relative != Path("."):
                self.initial_directories.add(relative.as_posix())
            for entry in self.vfs.entries(self.actor, current):
                child_relative = relative / entry.name
                if entry.collection:
                    pending.append(("/" + entry.path, child_relative))
                else:
                    targets.append((child_relative.as_posix(), self.vfs.read_bytes(self.actor, "/" + entry.path)))
        for relative, content in targets:
            total += len(content)
            if len(initial) >= self.max_files or total > self.max_bytes:
                raise RuntimeError("rsync staging limit exceeded")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            initial[relative] = hashlib.sha256(content).hexdigest()
        return initial

    @staticmethod
    def _safe_stage_files(staging: Path) -> dict[str, Path]:
        result: dict[str, Path] = {}
        for path in staging.rglob("*"):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise PermissionError("rsync links and special files are not accepted")
            if path.is_file():
                result[path.relative_to(staging).as_posix()] = path
        return result

    def _destination(self, relative: str) -> str:
        base = self.request.virtual_path.strip("/")
        return "/" + "/".join(part for part in (base, relative) if part and part != ".")

    def _commit(self, staging: Path, initial: dict[str, str]) -> None:
        if self.scope != "write":
            raise PermissionError("rsync key is read-only")
        files = self._safe_stage_files(staging)
        total = sum(path.stat().st_size for path in files.values())
        if len(files) > self.max_files or total > self.max_bytes:
            raise RuntimeError("rsync result exceeds configured limits")
        base = self.vfs.resolve(self.request.virtual_path)
        if not base.exists():
            self.vfs.mkdir(self.actor, self.request.virtual_path)
        # Create directories first through the audited VFS boundary.
        directories = sorted(
            (path for path in staging.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
        )
        for directory in directories:
            destination = self._destination(directory.relative_to(staging).as_posix())
            resource = self.vfs.resolve(destination)
            if not resource.exists():
                self.vfs.mkdir(self.actor, destination)
        scanner = AttachmentSecurity(self.vfs.root)
        for relative, staged in files.items():
            content = staged.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if initial.get(relative) == digest:
                continue
            if self.app.config.get("WEBDAV_UPLOAD_SCAN", False):
                scan = scanner.scan_webdav_upload(
                    content, self.actor, self._destination(relative), self.max_bytes,
                    source_type="rsync-write",
                )
                if scan.get("verdict") != "clean":
                    raise PermissionError("malware scan rejected the rsync upload")
            self.vfs.write_bytes(
                self.actor, self._destination(relative), content,
                expected_sha256=initial.get(relative, ""), max_bytes=self.max_bytes,
            )
        # Missing staged objects represent an explicit rsync delete operation.
        for relative in sorted(set(initial) - set(files), key=lambda value: value.count("/"), reverse=True):
            self.vfs.remove(
                self.actor, self._destination(relative), expected_sha256=initial[relative],
            )
        staged_directories = {
            path.relative_to(staging).as_posix() for path in directories
        }
        for relative in sorted(
            self.initial_directories - staged_directories,
            key=lambda value: value.count("/"), reverse=True,
        ):
            destination = self._destination(relative)
            resource = self.vfs.resolve(destination)
            if resource.exists() and resource != self.vfs.root:
                try:
                    self.vfs.remove(self.actor, destination)
                except OSError:
                    pass

    def run(self) -> None:
        status = 1
        try:
            if shutil.which("rsync") is None:
                raise RuntimeError("server-side rsync is not installed")
            if not self.request.sender and self.scope != "write":
                raise PermissionError("rsync key is read-only")
            with tempfile.TemporaryDirectory(prefix="simpleoffice-rsync-") as temporary:
                staging = Path(temporary)
                initial = self._materialize(self.request.virtual_path, staging)
                arguments = ["rsync", *self.request.arguments, ".", "."]
                process = subprocess.Popen(
                    arguments, cwd=staging, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, close_fds=True,
                )
                def inbound():
                    try:
                        while data := self.channel.recv(65536):
                            process.stdin.write(data)
                            process.stdin.flush()
                    except OSError:
                        pass
                    finally:
                        process.stdin.close()
                def outbound():
                    while data := process.stdout.read(65536):
                        self.channel.sendall(data)
                def drain_errors():
                    while process.stderr.read(65536):
                        pass
                input_thread = threading.Thread(target=inbound, daemon=True)
                output_thread = threading.Thread(target=outbound, daemon=True)
                error_thread = threading.Thread(target=drain_errors, daemon=True)
                input_thread.start(); output_thread.start(); error_thread.start()
                status = process.wait(timeout=self.timeout)
                output_thread.join(5)
                if status == 0 and not self.request.sender:
                    self._commit(staging, initial)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            status = 1
        finally:
            try:
                self.channel.send_exit_status(status)
                self.channel.shutdown_write()
                self.channel.close()
            except OSError:
                pass
