"""V2 StoragePort backed by BlobStore content and the logical ObjectCatalog."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.safe_paths import safe_filename

from ..blob_store import BlobIntegrityError, BlobStore, BlobVersion
from ..catalog import CatalogEntry, CatalogState, ObjectCatalog
from ..contracts import (
    AuditEvent,
    AuditPort,
    ErrorCode,
    LogicalObjectId,
    OperationResult,
    StorageLocation,
    StoredObject,
)
from .audit import RevisionHistoryAuditAdapter


class _LimitedReader:
    def __init__(self, source: BinaryIO, limit: int):
        if isinstance(limit, bool) or int(limit) < 0:
            raise ValueError("stream limit must be a non-negative integer")
        self.source = source
        self.limit = int(limit)
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        requested = self.limit - self.total + 1 if size is None or size < 0 else int(size)
        requested = max(1, min(requested, self.limit - self.total + 1))
        block = self.source.read(requested)
        if block is None:
            raise ValueError("storage stream returned no bytes")
        payload = bytes(block)
        if self.total + len(payload) > self.limit:
            raise ValueError("stream exceeds configured size limit")
        self.total += len(payload)
        return payload


class BlobCatalogStorageAdapter:
    """Authoritative V2 logical storage boundary.

    BlobStore owns physical content. ObjectCatalog owns namespace, visibility
    and the committed current version. A blob written before a catalog conflict
    is unreachable through this adapter and can be diagnosed/reclaimed later.
    """

    def __init__(
        self,
        root: str | Path,
        actor: str,
        *,
        audit_port: AuditPort | None = None,
        blob_store: BlobStore | None = None,
        catalog: ObjectCatalog | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.actor = str(actor or "").strip()
        if not self.actor:
            raise ValueError("storage adapter requires an actor")
        self.blobs = blob_store or BlobStore(self.root)
        self.catalog = catalog or ObjectCatalog(self.root)
        self.audit = audit_port or RevisionHistoryAuditAdapter(self.root)

    @staticmethod
    def _stored(entry: CatalogEntry) -> StoredObject:
        return StoredObject(
            object_id=entry.object_id,
            version=entry.version_id,
            size=entry.size,
            location=entry.location,
        )

    @staticmethod
    def _catalog_failure(result) -> OperationResult:
        if result.error is None:
            return OperationResult.failure(ErrorCode.INTERNAL_ERROR, "catalog operation failed")
        return OperationResult(error=result.error)

    @staticmethod
    def _failure(exc: Exception) -> OperationResult:
        if isinstance(exc, BlobIntegrityError):
            return OperationResult.failure(ErrorCode.INTEGRITY_ERROR, str(exc))
        if isinstance(exc, FileNotFoundError):
            return OperationResult.failure(ErrorCode.NOT_FOUND, str(exc))
        if isinstance(exc, OSError):
            return OperationResult.failure(ErrorCode.STORAGE_UNAVAILABLE, str(exc), retryable=True)
        return OperationResult.failure(ErrorCode.INVALID_INPUT, str(exc))

    def _active(self, object_id: LogicalObjectId) -> OperationResult[CatalogEntry]:
        result = self.catalog.get(object_id)
        if not result.ok:
            return self._catalog_failure(result)
        entry = result.value
        if entry.state is not CatalogState.ACTIVE:
            return OperationResult.failure(
                ErrorCode.CONFLICT,
                "catalog object requires recovery before normal access",
            )
        return OperationResult.success(entry)

    def _audit(self, entry: CatalogEntry, operation: str, **changes) -> OperationResult[str]:
        result = self.audit.append(
            AuditEvent(
                actor=self.actor,
                operation=operation,
                object_id=entry.object_id.value,
                occurred_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                source="v2-blob-catalog-storage",
                changes=changes,
            )
        )
        if result.ok:
            return result
        if entry.state is not CatalogState.DELETED:
            self.catalog.mark_recovery(entry.object_id)
        return OperationResult.failure(
            ErrorCode.STORAGE_UNAVAILABLE,
            "storage mutation was committed but audit persistence failed; object requires recovery",
            retryable=True,
        )

    @staticmethod
    def _candidate_locations(location: StorageLocation):
        path = Path(location.relative_path)
        yield location
        for index in range(2, 1002):
            suffix = "".join(path.suffixes)
            stem = path.name[:-len(suffix)] if suffix else path.name
            name = f"{stem}-{index}{suffix}"
            parent = path.parent.as_posix()
            yield StorageLocation(name if parent == "." else f"{parent}/{name}")

    def _register_version(
        self,
        object_id: LogicalObjectId,
        version: BlobVersion,
        location: StorageLocation,
        *,
        allow_rename: bool = False,
    ) -> OperationResult[CatalogEntry]:
        candidates = self._candidate_locations(location) if allow_rename else (location,)
        for candidate in candidates:
            result = self.catalog.register(
                object_id,
                candidate,
                version_id=version.version_id,
                size=version.size,
                content_sha256=version.content_sha256,
            )
            if result.ok:
                return result
            if result.error is None or result.error.code is not ErrorCode.CONFLICT or not allow_rename:
                return self._catalog_failure(result)
        return OperationResult.failure(ErrorCode.CONFLICT, "no collision-free storage location available")

    def _create_at(
        self,
        location: StorageLocation,
        content: bytes,
        *,
        allow_rename: bool = False,
        operation: str = "storage_created",
    ) -> OperationResult[StoredObject]:
        object_id = LogicalObjectId(str(uuid.uuid4()))
        try:
            version = self.blobs.write(object_id, bytes(content))
        except (BlobIntegrityError, OSError, ValueError, TypeError) as exc:
            return self._failure(exc)
        registered = self._register_version(
            object_id,
            version,
            location,
            allow_rename=allow_rename,
        )
        if not registered.ok:
            return self._catalog_failure(registered)
        entry = registered.value
        audit = self._audit(
            entry,
            operation,
            location=entry.location.relative_path,
            version=entry.version_id,
            size=entry.size,
        )
        if not audit.ok:
            return OperationResult(error=audit.error)
        return OperationResult.success(self._stored(entry))

    def read_bytes(self, object_id: LogicalObjectId) -> OperationResult[bytes]:
        current = self._active(object_id)
        if not current.ok:
            return self._catalog_failure(current)
        entry = current.value
        try:
            content = self.blobs.read(object_id, version_id=entry.version_id)
        except (BlobIntegrityError, OSError, ValueError, TypeError) as exc:
            return self._failure(exc)
        if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.content_sha256:
            return OperationResult.failure(
                ErrorCode.INTEGRITY_ERROR,
                "catalog content metadata does not match verified blob content",
            )
        return OperationResult.success(content)

    def create_bytes(self, location: StorageLocation, content: bytes) -> OperationResult[StoredObject]:
        return self._create_at(location, bytes(content))

    def import_stream(
        self,
        stream: BinaryIO,
        filename: str,
        *,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        object_id = LogicalObjectId(str(uuid.uuid4()))
        try:
            safe_name = safe_filename(filename, fallback="upload.bin", max_length=180)
            limited = _LimitedReader(stream, int(max_bytes))
            version = self.blobs.write_stream(object_id, limited)
            if archive:
                relative = f"archive/{version.content_sha256[:2]}/{version.content_sha256[2:4]}/{safe_name}"
            else:
                relative = f"inbox/{safe_name}"
            registered = self._register_version(
                object_id,
                version,
                StorageLocation(relative),
                allow_rename=True,
            )
        except (BlobIntegrityError, OSError, ValueError, TypeError) as exc:
            return self._failure(exc)
        if not registered.ok:
            return self._catalog_failure(registered)
        entry = registered.value
        audit = self._audit(
            entry,
            "storage_imported",
            location=entry.location.relative_path,
            version=entry.version_id,
            size=entry.size,
            archive=bool(archive),
        )
        if not audit.ok:
            return OperationResult(error=audit.error)
        return OperationResult.success(self._stored(entry))

    def replace_bytes(
        self,
        object_id: LogicalObjectId,
        content: bytes,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[StoredObject]:
        current = self._active(object_id)
        if not current.ok:
            return self._catalog_failure(current)
        previous = current.value
        if expected_version is not None and str(expected_version) != previous.version_id:
            return OperationResult.failure(ErrorCode.CONFLICT, "catalog object version changed")
        try:
            version = self.blobs.write(object_id, bytes(content))
        except (BlobIntegrityError, OSError, ValueError, TypeError) as exc:
            return self._failure(exc)
        updated = self.catalog.update_content(
            object_id,
            version_id=version.version_id,
            size=version.size,
            content_sha256=version.content_sha256,
            expected_version_id=previous.version_id,
        )
        if not updated.ok:
            return self._catalog_failure(updated)
        entry = updated.value
        audit = self._audit(
            entry,
            "storage_replaced",
            previous_version=previous.version_id,
            version=entry.version_id,
            size=entry.size,
        )
        if not audit.ok:
            return OperationResult(error=audit.error)
        return OperationResult.success(self._stored(entry))

    def copy(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        source = self.read_bytes(object_id)
        if not source.ok:
            return OperationResult(error=source.error)
        result = self._create_at(destination, source.value, operation="storage_copied")
        if result.ok and result.value.object_id == object_id:
            return OperationResult.failure(
                ErrorCode.INTEGRITY_ERROR,
                "copied object reused the source identity",
            )
        return result

    def delete(
        self,
        object_id: LogicalObjectId,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[str]:
        current = self._active(object_id)
        if not current.ok:
            return self._catalog_failure(current)
        entry = current.value
        if expected_version is not None and str(expected_version) != entry.version_id:
            return OperationResult.failure(ErrorCode.CONFLICT, "catalog object version changed")
        deleted = self.catalog.mark_deleted(object_id, expected_version_id=entry.version_id)
        if not deleted.ok:
            return self._catalog_failure(deleted)
        tombstone = deleted.value
        audit = self._audit(
            tombstone,
            "storage_deleted",
            location=tombstone.location.relative_path,
            version=tombstone.version_id,
        )
        if not audit.ok:
            return OperationResult(error=audit.error)
        return OperationResult.success(entry.version_id)

    def move(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        current = self._active(object_id)
        if not current.ok:
            return self._catalog_failure(current)
        previous = current.value
        moved = self.catalog.move(
            object_id,
            destination,
            expected_version_id=previous.version_id,
        )
        if not moved.ok:
            return self._catalog_failure(moved)
        entry = moved.value
        audit = self._audit(
            entry,
            "storage_moved",
            previous_location=previous.location.relative_path,
            location=entry.location.relative_path,
            version=entry.version_id,
        )
        if not audit.ok:
            return OperationResult(error=audit.error)
        return OperationResult.success(self._stored(entry))
