"""Independent recovery for the encrypted V2 blob format.

This module deliberately depends only on the encrypted store and portable
master-key recovery material. It does not require Flask, the user database,
ObjectCatalog, the legacy DocumentStore, or the normal runtime unlock secret.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import LogicalObjectId
from .encrypted_blob_store import (
    EncryptedBlobIntegrityError,
    EncryptedBlobStore,
    EncryptedBlobVersion,
)
from .master_keys import (
    load_recovery_bundle_file,
    load_recovery_key_file,
    recover_master_key_from_bundle,
    recovery_bundle_info,
)
from .runtime_keys import STORAGE_PROFILE_ID


@dataclass(frozen=True)
class EncryptedRecoveryCheck:
    object_id: str
    version_id: str
    valid: bool
    size: int = 0
    content_sha256: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _storage_profile_hash() -> str:
    return hashlib.sha256(STORAGE_PROFILE_ID.encode("utf-8")).hexdigest()


def recover_storage_master_key(
    bundle_path: str | Path,
    recovery_key_path: str | Path,
    *,
    forbidden_root: str | Path | None = None,
) -> bytes:
    """Recover only the dedicated local-storage master key from portable files."""

    if forbidden_root is not None:
        root = Path(forbidden_root).expanduser().resolve()
        for value, label in (
            (bundle_path, "recovery bundle"),
            (recovery_key_path, "recovery key"),
        ):
            supplied = Path(value).expanduser()
            if supplied.is_symlink():
                raise ValueError(f"{label} must not be a symlink")
            try:
                resolved = supplied.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"{label} is unavailable") from exc
            if resolved == root or root in resolved.parents:
                raise ValueError(f"{label} must be stored outside the SimpleOffice data root")

    bundle = load_recovery_bundle_file(bundle_path)
    info = recovery_bundle_info(bundle)
    if str(info.get("profile_hash") or "") != _storage_profile_hash():
        raise ValueError("recovery bundle does not belong to the V2 local-storage profile")
    recovery_key = load_recovery_key_file(recovery_key_path)
    return recover_master_key_from_bundle(bundle, recovery_key)


class EncryptedRecoveryService:
    """Read/verify/export encrypted objects without the normal application."""

    def __init__(self, root: str | Path, master_key: bytes):
        self.root = Path(root).expanduser().resolve()
        self.store = EncryptedBlobStore(
            self.root,
            bytes(master_key),
            initialize=False,
        )

    def inventory(self) -> dict[str, Any]:
        result = dict(self.store.inventory())
        version_files = [
            path for path in self.store.versions.glob("*.json")
            if path.is_file() and not path.is_symlink()
        ]
        result.update({
            "recovery_mode": "encrypted-independent",
            "version_files": len(version_files),
            "requires_flask": False,
            "requires_user_database": False,
            "requires_object_catalog": False,
            "requires_runtime_password_file": False,
        })
        return result

    def verify(
        self,
        object_id: str,
        version_id: str | None = None,
    ) -> EncryptedRecoveryCheck:
        logical = LogicalObjectId(str(object_id))
        try:
            version = self.store.verify(
                logical,
                version_id=str(version_id) if version_id else None,
            )
        except (EncryptedBlobIntegrityError, OSError, TypeError, ValueError) as exc:
            return EncryptedRecoveryCheck(
                object_id=logical.value,
                version_id=str(version_id or ""),
                valid=False,
                error=str(exc),
            )
        return self._check(version)

    def verify_all(self) -> list[EncryptedRecoveryCheck]:
        results: list[EncryptedRecoveryCheck] = []
        for path in sorted(self.store.versions.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                results.append(EncryptedRecoveryCheck(
                    object_id="",
                    version_id=path.stem,
                    valid=False,
                    error="encrypted version metadata is not a regular file",
                ))
                continue
            try:
                version_id = str(uuid.UUID(path.stem))
                manifest = self.store.version_manifest(version_id)
                logical = LogicalObjectId(str(manifest.get("object_id") or ""))
                version = self.store.verify(logical, version_id=version_id)
                results.append(self._check(version))
            except (EncryptedBlobIntegrityError, OSError, TypeError, ValueError) as exc:
                results.append(EncryptedRecoveryCheck(
                    object_id="",
                    version_id=path.stem,
                    valid=False,
                    error=str(exc),
                ))
        return results

    def export(
        self,
        object_id: str,
        output: str | Path,
        *,
        version_id: str | None = None,
        overwrite: bool = False,
    ) -> Path:
        logical = LogicalObjectId(str(object_id))
        target = self._output_path(output, overwrite=overwrite)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                version = self.store.copy_verified_to(
                    logical,
                    handle,
                    version_id=str(version_id) if version_id else None,
                )
                handle.flush()
                os.fsync(handle.fileno())
            if version.object_id != logical:
                raise EncryptedBlobIntegrityError("recovered object identity changed")
            if overwrite:
                os.replace(temporary, target)
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise FileExistsError("recovery output already exists") from exc
                temporary.unlink()
            if os.name == "posix":
                try:
                    directory = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError:
                    pass
            return target
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _check(version: EncryptedBlobVersion) -> EncryptedRecoveryCheck:
        return EncryptedRecoveryCheck(
            object_id=version.object_id.value,
            version_id=version.version_id,
            valid=True,
            size=version.size,
            content_sha256=version.content_sha256,
        )

    def _output_path(self, output: str | Path, *, overwrite: bool) -> Path:
        supplied = Path(output).expanduser()
        if supplied.name in {"", ".", ".."}:
            raise ValueError("recovery output path is invalid")
        parent_input = supplied.parent
        if parent_input.is_symlink():
            raise ValueError("recovery output parent must not be a symlink")
        try:
            parent = parent_input.resolve(strict=True)
        except OSError as exc:
            raise ValueError("recovery output parent does not exist") from exc
        if not parent.is_dir():
            raise ValueError("recovery output parent must be a directory")
        target = parent / supplied.name
        if target == self.root or self.root in target.parents:
            raise ValueError("recovered plaintext must be exported outside the SimpleOffice data root")
        if target.is_symlink():
            raise ValueError("recovery output must not be a symlink")
        if target.exists():
            if not overwrite:
                raise FileExistsError("recovery output already exists")
            if not target.is_file():
                raise ValueError("recovery output target must be a regular file")
        return target
