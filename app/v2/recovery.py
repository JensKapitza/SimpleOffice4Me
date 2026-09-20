"""Independent read-mostly recovery service for V2 blob stores."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .blob_store import BlobIntegrityError, BlobStore
from .contracts import LogicalObjectId


RECOVERY_DESCRIPTOR = "simpleoffice-v2-recovery/v1"


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class VerificationResult:
    object_id: str
    version_id: str
    valid: bool
    size: int = 0
    error: str = ""


class RecoveryService:
    """Recovery operations that do not require Flask, a user DB or request state."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.store = BlobStore(self.root)

    def inventory(self) -> dict[str, Any]:
        return self.store.inventory()

    def verify(self, object_id: str, version_id: str | None = None) -> VerificationResult:
        logical = LogicalObjectId(object_id)
        try:
            payload = self.store.read(logical, version_id=version_id)
            manifest = self.store.version_manifest(version_id) if version_id else self.store.current_manifest(logical)
            return VerificationResult(
                object_id=logical.value,
                version_id=str(manifest["version_id"]),
                valid=True,
                size=len(payload),
            )
        except (BlobIntegrityError, OSError, ValueError) as exc:
            return VerificationResult(
                object_id=logical.value,
                version_id=str(version_id or ""),
                valid=False,
                error=str(exc),
            )

    def verify_all(self) -> list[VerificationResult]:
        results: list[VerificationResult] = []
        for path in sorted(self.store.versions.glob("*.json")):
            version_id = path.stem
            try:
                manifest = self.store.version_manifest(version_id)
                object_id = str(manifest["object_id"])
            except (BlobIntegrityError, KeyError, OSError, ValueError) as exc:
                results.append(VerificationResult("", version_id, False, error=str(exc)))
                continue
            results.append(self.verify(object_id, version_id))
        return results

    def descriptor(self, object_id: str, version_id: str | None = None) -> dict[str, Any]:
        logical = LogicalObjectId(object_id)
        manifest = self.store.version_manifest(version_id) if version_id else self.store.current_manifest(logical)
        if manifest.get("object_id") != logical.value:
            raise BlobIntegrityError("recovery descriptor object identity mismatch")
        manifest_digest = hashlib.sha256(_canonical(manifest)).hexdigest()
        return {
            "format": RECOVERY_DESCRIPTOR,
            "object_id": logical.value,
            "version_id": str(manifest["version_id"]),
            "blob_format": dict(manifest["format"]),
            "size": int(manifest["size"]),
            "content_sha256": str(manifest["content_sha256"]),
            "chunk_count": len(manifest["chunks"]),
            "manifest_sha256": manifest_digest,
        }

    def verify_descriptor(self, descriptor: dict[str, Any]) -> VerificationResult:
        if descriptor.get("format") != RECOVERY_DESCRIPTOR:
            raise ValueError("unsupported recovery descriptor")
        object_id = LogicalObjectId(str(descriptor.get("object_id") or ""))
        version_id = str(descriptor.get("version_id") or "")
        manifest = self.store.version_manifest(version_id)
        digest = hashlib.sha256(_canonical(manifest)).hexdigest()
        if digest != descriptor.get("manifest_sha256"):
            return VerificationResult(object_id.value, version_id, False, error="manifest digest mismatch")
        if (
            manifest.get("object_id") != object_id.value
            or int(manifest.get("size", -1)) != int(descriptor.get("size", -2))
            or manifest.get("content_sha256") != descriptor.get("content_sha256")
            or len(manifest.get("chunks", [])) != int(descriptor.get("chunk_count", -1))
        ):
            return VerificationResult(object_id.value, version_id, False, error="descriptor metadata mismatch")
        return self.verify(object_id.value, version_id)

    def export(
        self,
        object_id: str,
        output: str | Path,
        *,
        version_id: str | None = None,
        overwrite: bool = False,
    ) -> Path:
        logical = LogicalObjectId(object_id)
        content = self.store.read(logical, version_id=version_id)
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError("recovery export target already exists")
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
