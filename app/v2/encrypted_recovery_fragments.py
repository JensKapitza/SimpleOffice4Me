"""Private cache for descriptor-bound encrypted recovery fragments.

Foreign recovery ciphertext never becomes part of the authoritative local blob
store merely because a peer supplied it.  This cache stores only chunks that
match an already validated portable recovery descriptor exactly.
"""
from __future__ import annotations

import hashlib
import os
import stat
import uuid
from pathlib import Path
from typing import Any, Mapping

from .encrypted_recovery_descriptor import validate_encrypted_recovery_descriptor


class EncryptedRecoveryFragmentStore:
    def __init__(self, root: str | Path):
        source = Path(root).expanduser().resolve()
        self.base = source / ".simpleoffice-v2" / "recovery-fragments"
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(self.base, 0o700)

    @staticmethod
    def _binding(
        descriptor: Mapping[str, Any],
        index: int,
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        checked = validate_encrypted_recovery_descriptor(descriptor)
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("encrypted recovery chunk index must be an integer")
        refs = checked["ciphertext_chunks"]
        if index < 0 or index >= len(refs):
            raise ValueError("encrypted recovery chunk index is outside the descriptor")
        return checked, refs[index], index

    def _directory(self, descriptor_id: str) -> Path:
        path = self.base / str(descriptor_id)
        path.mkdir(exist_ok=True, mode=0o700)
        if path.is_symlink() or not path.is_dir():
            raise OSError("encrypted recovery fragment directory is unavailable")
        if os.name == "posix":
            os.chmod(path, 0o700)
        return path

    def _path(self, descriptor_id: str, index: int) -> Path:
        return self.base / str(descriptor_id) / f"{int(index):08d}.bin"

    @staticmethod
    def _verified_bytes(path: Path, row: Mapping[str, Any]) -> bytes:
        try:
            expected_size = int(row.get("ciphertext_size", -1))
            expected_digest = str(row.get("ciphertext_sha256") or "").casefold()
        except (TypeError, ValueError) as exc:
            raise ValueError("encrypted recovery chunk metadata is invalid") from exc
        if expected_size < 16 or len(expected_digest) != 64:
            raise ValueError("encrypted recovery chunk metadata is invalid")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise OSError("encrypted recovery fragment is unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
                raise RuntimeError("encrypted recovery fragment size mismatch")
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            remaining = expected_size
            while remaining:
                block = os.read(descriptor, min(1024 * 1024, remaining))
                if not block:
                    raise RuntimeError("encrypted recovery fragment ended early")
                chunks.append(block)
                digest.update(block)
                remaining -= len(block)
            if os.read(descriptor, 1):
                raise RuntimeError("encrypted recovery fragment contains trailing bytes")
            if digest.hexdigest() != expected_digest:
                raise RuntimeError("encrypted recovery fragment digest mismatch")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def contains(self, descriptor: Mapping[str, Any], index: int) -> bool:
        try:
            checked, row, normalized = self._binding(descriptor, index)
            path = self._path(checked["descriptor_id"], normalized)
            self._verified_bytes(path, row)
            return True
        except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def read(self, descriptor: Mapping[str, Any], index: int) -> bytes:
        checked, row, normalized = self._binding(descriptor, index)
        return self._verified_bytes(
            self._path(checked["descriptor_id"], normalized),
            row,
        )

    def write(
        self,
        descriptor: Mapping[str, Any],
        index: int,
        content: bytes,
    ) -> Path:
        checked, row, normalized = self._binding(descriptor, index)
        payload = bytes(content)
        expected_size = int(row["ciphertext_size"])
        expected_digest = str(row["ciphertext_sha256"])
        if len(payload) != expected_size:
            raise ValueError("encrypted recovery fragment size does not match descriptor")
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ValueError("encrypted recovery fragment digest does not match descriptor")

        directory = self._directory(checked["descriptor_id"])
        target = directory / f"{normalized:08d}.bin"
        if target.is_symlink():
            raise OSError("encrypted recovery fragment target must not be a symlink")
        if target.exists():
            try:
                if self._verified_bytes(target, row) == payload:
                    return target
            except (OSError, RuntimeError, ValueError):
                pass

        temporary = directory / f".{normalized:08d}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor_fd = os.open(temporary, flags, 0o600)
        published = False
        try:
            with os.fdopen(descriptor_fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            published = True
            if os.name == "posix":
                os.chmod(target, 0o600)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if not published:
                temporary.unlink(missing_ok=True)
        return target
