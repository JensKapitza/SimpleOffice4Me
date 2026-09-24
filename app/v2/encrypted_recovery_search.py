"""Authorized local availability checks for encrypted recovery chunks.

The service never accepts raw physical chunk IDs independently. Callers supply a
validated portable recovery descriptor and select chunk indexes from that exact
descriptor. HTTP authorization binds access to its descriptor_id separately.
"""
from __future__ import annotations

import hashlib
import os
import stat
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from .encrypted_recovery_descriptor import validate_encrypted_recovery_descriptor


AVAILABILITY_FORMAT = "simpleoffice-v2-encrypted-recovery-availability/v1"
MAX_QUERY_CHUNKS = 256


class EncryptedRecoveryChunkSearch:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.chunks = (
            self.root
            / ".simpleoffice-v2"
            / "encrypted-blob-store"
            / "chunks"
        )

    @staticmethod
    def _indexes(
        total: int,
        requested: Iterable[int] | None,
    ) -> tuple[int, ...]:
        if requested is None:
            if total > MAX_QUERY_CHUNKS:
                raise ValueError(
                    "explicit encrypted recovery chunk indexes are required for large descriptors"
                )
            return tuple(range(total))
        if isinstance(requested, (str, bytes)):
            raise ValueError("encrypted recovery chunk indexes must be an iterable")
        indexes: set[int] = set()
        for raw in requested:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ValueError("encrypted recovery chunk index must be an integer")
            if raw < 0 or raw >= total:
                raise ValueError("encrypted recovery chunk index is outside the descriptor")
            indexes.add(raw)
            if len(indexes) > MAX_QUERY_CHUNKS:
                raise ValueError("too many encrypted recovery chunks requested")
        if not indexes:
            raise ValueError("at least one encrypted recovery chunk index is required")
        return tuple(sorted(indexes))

    def _chunk_matches(self, row: Mapping[str, Any]) -> bool:
        if self.chunks.is_symlink() or not self.chunks.is_dir():
            return False
        try:
            name = uuid.UUID(str(row.get("physical_id") or "")).hex + ".bin"
            expected_size = int(row.get("ciphertext_size", -1))
            expected_digest = str(row.get("ciphertext_sha256") or "").casefold()
        except (TypeError, ValueError):
            return False
        if expected_size < 16 or len(expected_digest) != 64:
            return False

        path = self.chunks / name
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return False
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
                return False
            digest = hashlib.sha256()
            remaining = expected_size
            while remaining:
                block = os.read(descriptor, min(1024 * 1024, remaining))
                if not block:
                    return False
                digest.update(block)
                remaining -= len(block)
            if os.read(descriptor, 1):
                return False
            return digest.hexdigest() == expected_digest
        finally:
            os.close(descriptor)

    def availability(
        self,
        descriptor: Mapping[str, Any],
        *,
        chunk_indexes: Iterable[int] | None = None,
    ) -> dict[str, Any]:
        checked = validate_encrypted_recovery_descriptor(descriptor)
        refs = checked["ciphertext_chunks"]
        indexes = self._indexes(len(refs), chunk_indexes)
        available = tuple(
            index for index in indexes
            if self._chunk_matches(refs[index])
        )
        available_set = set(available)
        missing = tuple(index for index in indexes if index not in available_set)
        return {
            "format": AVAILABILITY_FORMAT,
            "descriptor_id": checked["descriptor_id"],
            "object_id": checked["object_id"],
            "version_id": checked["version_id"],
            "total_chunks": len(refs),
            "requested_indexes": list(indexes),
            "available_indexes": list(available),
            "missing_indexes": list(missing),
        }
