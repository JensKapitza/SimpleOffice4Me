"""Independent recovery helpers for the encrypted V2 blob store."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from .contracts import LogicalObjectId
from .encrypted_blob_store import EncryptedBlobStore
from .master_keys import (
    load_recovery_bundle_file,
    load_recovery_key_file,
    recover_master_key_from_bundle,
    recovery_bundle_info,
)


class EncryptedBlobRecoveryService:
    """Open encrypted V2 blobs from portable offline recovery material."""

    def __init__(
        self,
        root: str | Path,
        recovery_bundle: str | Path,
        recovery_key_file: str | Path,
    ):
        self.root = Path(root).expanduser().resolve()
        bundle = load_recovery_bundle_file(recovery_bundle)
        recovery_key = load_recovery_key_file(recovery_key_file)
        self.bundle_info = recovery_bundle_info(bundle)
        master_key = recover_master_key_from_bundle(bundle, recovery_key)
        try:
            self.store = EncryptedBlobStore(
                self.root,
                master_key,
                initialize=False,
            )
        finally:
            del master_key
            del recovery_key

    @staticmethod
    def _logical(value: str) -> LogicalObjectId:
        return LogicalObjectId(str(value or "").strip())

    def inventory(
        self,
        *,
        offset: int = 0,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Return physical inventory plus DB-independent recoverable version refs."""

        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("encrypted recovery inventory offset must be non-negative")
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 10000):
            raise ValueError("encrypted recovery inventory limit must be between 1 and 10000")

        inventory = self.store.inventory()
        paths = sorted(self.store.versions.glob("*.json"))
        selected = paths[offset:offset + limit]
        current_cache: dict[str, str] = {}
        versions: list[dict[str, Any]] = []
        skipped: list[str] = []
        for path in selected:
            version_id = path.stem
            try:
                manifest = self.store.version_manifest(version_id)
                logical = self._logical(str(manifest.get("object_id") or ""))
                if logical.value not in current_cache:
                    try:
                        current_cache[logical.value] = str(
                            self.store.current_manifest(logical)["version_id"]
                        )
                    except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError):
                        current_cache[logical.value] = ""
                versions.append({
                    "object_id": logical.value,
                    "version_id": version_id,
                    "created_at": int(manifest.get("created_at") or 0),
                    "chunk_count": len(manifest.get("chunks") or []),
                    "current": current_cache[logical.value] == version_id,
                })
            except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError):
                skipped.append(path.name)

        next_offset = offset + len(selected)
        return {
            **inventory,
            "recovery_profile_hash": self.bundle_info["profile_hash"],
            "recovery_key_authenticated": True,
            "store_modified": False,
            "recoverable_versions": versions,
            "recovery_index_offset": offset,
            "recovery_index_limit": limit,
            "recovery_index_total": len(paths),
            "recovery_index_skipped": skipped,
            "recovery_index_next_offset": (
                next_offset if next_offset < len(paths) else None
            ),
        }

    def verify(
        self,
        object_id: str,
        *,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        logical = self._logical(object_id)
        version = self.store.verify(
            logical,
            version_id=version_id or None,
        )
        return {
            "valid": True,
            "object_id": logical.value,
            "version_id": version.version_id,
            "size": version.size,
            "chunk_count": version.chunk_count,
            "master_key_exported": False,
            "store_modified": False,
        }

    def _output_path(
        self,
        output: str | Path,
        *,
        overwrite: bool,
    ) -> Path:
        supplied = Path(output).expanduser()
        if supplied.name in {"", ".", ".."}:
            raise ValueError("encrypted recovery output path is invalid")
        try:
            parent = supplied.parent.resolve(strict=True)
        except OSError as exc:
            raise ValueError("encrypted recovery output parent is unavailable") from exc
        if not parent.is_dir() or parent.is_symlink():
            raise ValueError("encrypted recovery output parent must be a real directory")
        target = parent / supplied.name
        if target == self.root or self.root in target.parents:
            raise ValueError("encrypted recovery output must be outside the SimpleOffice data root")
        if target.is_symlink():
            raise ValueError("encrypted recovery output must not be a symlink")
        if target.exists():
            if not overwrite:
                raise FileExistsError("encrypted recovery output already exists")
            if not target.is_file():
                raise ValueError("encrypted recovery output must be a regular file")
        return target

    def export(
        self,
        object_id: str,
        output: str | Path,
        *,
        version_id: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Verify into a private temporary file and publish atomically on success."""

        logical = self._logical(object_id)
        target = self._output_path(output, overwrite=overwrite)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        published = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                version = self.store.copy_verified_to(
                    logical,
                    handle,
                    version_id=version_id or None,
                )
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(temporary, target)
            else:
                # Preserve the no-overwrite contract even if another process
                # creates the destination after the initial path check.
                os.link(temporary, target)
                temporary.unlink()
            published = True
            if os.name == "posix":
                os.chmod(target, 0o600)
                directory = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if not published:
                temporary.unlink(missing_ok=True)

        return {
            "exported": True,
            "object_id": logical.value,
            "version_id": version.version_id,
            "size": version.size,
            "chunk_count": version.chunk_count,
            "output": str(target),
            "master_key_exported": False,
            "recovery_key_exported": False,
        }
