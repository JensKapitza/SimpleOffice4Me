"""V2-authoritative StoragePort with a synchronized V1 compatibility projection.

The BlobStore/ObjectCatalog pair is the content and namespace authority.  The
legacy DocumentStore remains a compatibility projection while UI/search and
older protocol code still consume its metadata.  Mutations commit V2 first and
roll the catalog back when the projection cannot be updated.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.document_store import DocumentStore

from ..catalog import CatalogEntry, ObjectCatalog
from ..contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation, StoredObject
from .blob_catalog import BlobCatalogStorageAdapter
from .document_store import DocumentStoreStorageAdapter


class V2AuthoritativeStorageAdapter:
    def __init__(
        self,
        root: str | Path,
        actor: str,
        *,
        primary: BlobCatalogStorageAdapter | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.actor = str(actor or "").strip()
        if not self.actor:
            raise ValueError("storage adapter requires an actor")
        self.primary = primary or BlobCatalogStorageAdapter(self.root, self.actor)
        self.catalog: ObjectCatalog = self.primary.catalog
        self.legacy = DocumentStoreStorageAdapter(self.root, self.actor)
        self.store: DocumentStore = self.legacy.store

    @staticmethod
    def _error(code: ErrorCode, message: str, *, retryable: bool = False):
        return OperationResult.failure(code, message, retryable=retryable)

    @staticmethod
    def _projection_failure(exc: Exception):
        result = DocumentStoreStorageAdapter._failure(exc)
        return OperationResult(error=result.error)

    def _entry(self, object_id: LogicalObjectId) -> OperationResult[CatalogEntry]:
        result = self.catalog.get(object_id)
        if result.ok:
            return result
        return OperationResult(error=result.error)

    @staticmethod
    def _compat(entry: CatalogEntry) -> StoredObject:
        return StoredObject(
            object_id=entry.object_id,
            version=entry.content_sha256,
            size=entry.size,
            location=entry.location,
        )

    def _compat_result(self, object_id: LogicalObjectId) -> OperationResult[StoredObject]:
        entry = self._entry(object_id)
        if not entry.ok:
            return OperationResult(error=entry.error)
        return OperationResult.success(self._compat(entry.value))

    def _expected_version(
        self,
        entry: CatalogEntry,
        expected: str | None,
    ) -> OperationResult[str]:
        if expected is None or str(expected) in {entry.version_id, entry.content_sha256}:
            return OperationResult.success(entry.version_id)
        return self._error(ErrorCode.CONFLICT, "document content changed since it was opened")

    def _projection_preflight(
        self,
        entry: CatalogEntry,
        *,
        editable: bool = True,
    ) -> OperationResult[dict]:
        try:
            metadata = self.store.get_document(entry.object_id.value)
            if editable:
                self.store._require_document_editable(metadata)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._projection_failure(exc)
        if str(metadata.get("last_path") or "") != entry.location.relative_path:
            return self._error(ErrorCode.INTEGRITY_ERROR, "legacy projection location differs from V2")
        if str(metadata.get("sha256") or "") != entry.content_sha256:
            return self._error(ErrorCode.INTEGRITY_ERROR, "legacy projection digest differs from V2")
        content = self.legacy.read_bytes(entry.object_id)
        if not content.ok:
            return OperationResult(error=content.error)
        if len(content.value) != entry.size or hashlib.sha256(content.value).hexdigest() != entry.content_sha256:
            return self._error(ErrorCode.INTEGRITY_ERROR, "legacy projection content differs from V2")
        return OperationResult.success(metadata)

    def _projection_destination(self, location: StorageLocation) -> OperationResult[Path]:
        try:
            relative = self.store._safe_managed_relative_path(location.relative_path, require_name=True)
            target = self.root / relative
            if not target.parent.is_dir() or target.parent.is_symlink():
                return self._error(ErrorCode.NOT_FOUND, "destination collection does not exist")
            if target.exists():
                return self._error(ErrorCode.CONFLICT, "destination resource already exists")
            return OperationResult.success(target)
        except (OSError, ValueError) as exc:
            return self._projection_failure(exc)

    def _discard_new(self, stored: StoredObject) -> bool:
        result = self.catalog.mark_deleted(
            stored.object_id,
            expected_version_id=stored.version,
        )
        return bool(result.ok)

    def _restore_content(self, previous: CatalogEntry, current_version: str) -> bool:
        result = self.catalog.update_content(
            previous.object_id,
            version_id=previous.version_id,
            size=previous.size,
            content_sha256=previous.content_sha256,
            expected_version_id=current_version,
        )
        return bool(result.ok)

    def _projection_create(
        self,
        stored: StoredObject,
        content: bytes,
    ) -> OperationResult[StoredObject]:
        try:
            metadata = self.store.create_document_at(
                stored.location.relative_path,
                content,
                self.actor,
                document_id=stored.object_id.value,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            rolled_back = self._discard_new(stored)
            if not rolled_back:
                return self._error(
                    ErrorCode.STORAGE_UNAVAILABLE,
                    "V2 create committed but compatibility projection and rollback failed",
                    retryable=True,
                )
            return self._projection_failure(exc)
        if metadata.get("document_id") != stored.object_id.value:
            return self._error(ErrorCode.INTEGRITY_ERROR, "compatibility projection changed object identity")
        return self._compat_result(stored.object_id)

    def read_bytes(self, object_id: LogicalObjectId) -> OperationResult[bytes]:
        return self.primary.read_bytes(object_id)

    def create_bytes(
        self,
        location: StorageLocation,
        content: bytes,
    ) -> OperationResult[StoredObject]:
        destination = self._projection_destination(location)
        if not destination.ok:
            return OperationResult(error=destination.error)
        primary = self.primary.create_bytes(location, bytes(content))
        if not primary.ok:
            return OperationResult(error=primary.error)
        return self._projection_create(primary.value, bytes(content))

    def import_stream(
        self,
        stream: BinaryIO,
        filename: str,
        *,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            return self._error(ErrorCode.INVALID_INPUT, "upload size limit must be positive")
        source = getattr(stream, "stream", stream)
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as spool:
            total = 0
            while True:
                block = source.read(1024 * 1024)
                if block is None:
                    return self._error(ErrorCode.INVALID_INPUT, "storage stream returned no bytes")
                block = bytes(block)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    return self._error(ErrorCode.INVALID_INPUT, "stream exceeds configured size limit")
                spool.write(block)
            spool.seek(0)
            primary = self.primary.import_stream(
                spool,
                filename,
                archive=archive,
                max_bytes=max_bytes,
            )
            if not primary.ok:
                return OperationResult(error=primary.error)
            stored = primary.value
            target = self.root / stored.location.relative_path
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                self.store.ensure_folder_policy(target.parent)
                spool.seek(0)
                metadata = self.store.create_document_stream_at(
                    stored.location.relative_path,
                    spool,
                    self.actor,
                    max_bytes=max_bytes,
                    document_id=stored.object_id.value,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                rolled_back = self._discard_new(stored)
                if not rolled_back:
                    return self._error(
                        ErrorCode.STORAGE_UNAVAILABLE,
                        "V2 import committed but compatibility projection and rollback failed",
                        retryable=True,
                    )
                return self._projection_failure(exc)
        if metadata.get("document_id") != stored.object_id.value:
            return self._error(ErrorCode.INTEGRITY_ERROR, "compatibility projection changed object identity")
        return self._compat_result(stored.object_id)

    def replace_bytes(
        self,
        object_id: LogicalObjectId,
        content: bytes,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[StoredObject]:
        current = self._entry(object_id)
        if not current.ok:
            return OperationResult(error=current.error)
        previous = current.value
        expected = self._expected_version(previous, expected_version)
        if not expected.ok:
            return OperationResult(error=expected.error)
        projection = self._projection_preflight(previous)
        if not projection.ok:
            return OperationResult(error=projection.error)
        primary = self.primary.replace_bytes(
            object_id,
            bytes(content),
            expected_version=previous.version_id,
        )
        if not primary.ok:
            return OperationResult(error=primary.error)
        try:
            self.store.replace_content(
                object_id.value,
                bytes(content),
                self.actor,
                expected_sha256=previous.content_sha256,
                source="v2-authoritative-projection",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            rolled_back = self._restore_content(previous, primary.value.version)
            if not rolled_back:
                self.catalog.mark_recovery(object_id)
                return self._error(
                    ErrorCode.STORAGE_UNAVAILABLE,
                    "V2 replace committed but compatibility projection and rollback failed",
                    retryable=True,
                )
            return self._projection_failure(exc)
        return self._compat_result(object_id)

    def copy(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        current = self._entry(object_id)
        if not current.ok:
            return OperationResult(error=current.error)
        projection = self._projection_preflight(current.value)
        if not projection.ok:
            return OperationResult(error=projection.error)
        target = self._projection_destination(destination)
        if not target.ok:
            return OperationResult(error=target.error)
        primary = self.primary.copy(object_id, destination)
        if not primary.ok:
            return OperationResult(error=primary.error)
        try:
            metadata = self.store.copy_document(
                object_id.value,
                primary.value.location.relative_path,
                self.actor,
                document_id=primary.value.object_id.value,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            rolled_back = self._discard_new(primary.value)
            if not rolled_back:
                return self._error(
                    ErrorCode.STORAGE_UNAVAILABLE,
                    "V2 copy committed but compatibility projection and rollback failed",
                    retryable=True,
                )
            return self._projection_failure(exc)
        if metadata.get("document_id") != primary.value.object_id.value:
            return self._error(ErrorCode.INTEGRITY_ERROR, "compatibility copy changed object identity")
        return self._compat_result(primary.value.object_id)

    def move(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        current = self._entry(object_id)
        if not current.ok:
            return OperationResult(error=current.error)
        previous = current.value
        projection = self._projection_preflight(previous)
        if not projection.ok:
            return OperationResult(error=projection.error)
        target = self._projection_destination(destination)
        if not target.ok:
            return OperationResult(error=target.error)
        primary = self.primary.move(object_id, destination)
        if not primary.ok:
            return OperationResult(error=primary.error)
        target_path = Path(primary.value.location.relative_path)
        try:
            self.store.move_document(
                object_id.value,
                target_path.parent.as_posix() if target_path.parent.as_posix() != "." else "",
                self.actor,
                destination_name=target_path.name,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            rollback = self.catalog.move(
                object_id,
                previous.location,
                expected_version_id=previous.version_id,
            )
            if not rollback.ok:
                self.catalog.mark_recovery(object_id)
                return self._error(
                    ErrorCode.STORAGE_UNAVAILABLE,
                    "V2 move committed but compatibility projection and rollback failed",
                    retryable=True,
                )
            return self._projection_failure(exc)
        return self._compat_result(object_id)

    def delete(
        self,
        object_id: LogicalObjectId,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[str]:
        current = self._entry(object_id)
        if not current.ok:
            return OperationResult(error=current.error)
        previous = current.value
        expected = self._expected_version(previous, expected_version)
        if not expected.ok:
            return OperationResult(error=expected.error)
        projection = self._projection_preflight(previous)
        if not projection.ok:
            return OperationResult(error=projection.error)
        primary = self.primary.delete(object_id, expected_version=previous.version_id)
        if not primary.ok:
            return OperationResult(error=primary.error)
        try:
            self.store.soft_delete_document(
                object_id.value,
                self.actor,
                expected_sha256=previous.content_sha256,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            rollback = self.catalog.restore(object_id, location=previous.location)
            if not rollback.ok:
                return self._error(
                    ErrorCode.STORAGE_UNAVAILABLE,
                    "V2 delete committed but compatibility projection and rollback failed",
                    retryable=True,
                )
            return self._projection_failure(exc)
        return OperationResult.success(previous.content_sha256)
