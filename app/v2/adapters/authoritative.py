"""Projection-free V2-authoritative StoragePort.

ObjectCatalog/BlobStore own content and namespace. The legacy DocumentStore is
retained only as a metadata/search/UI read model; document payload bytes are
never projected into the presentation filesystem.
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from ..catalog import CatalogEntry, ObjectCatalog
from ..contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation, StoredObject
from ..metadata_projection import V2MetadataProjection
from .blob_catalog import BlobCatalogStorageAdapter


class V2AuthoritativeStorageAdapter:
    requires_legacy_projection = False
    requires_metadata_projection = True

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
        self.projection = V2MetadataProjection(self.root, self.actor)
        self.store = self.projection.store

    @staticmethod
    def _error(code: ErrorCode, message: str, *, retryable: bool = False):
        return OperationResult.failure(code, message, retryable=retryable)

    @staticmethod
    def _projection_failure(exc: Exception):
        if isinstance(exc, FileNotFoundError):
            code = ErrorCode.NOT_FOUND
        elif isinstance(exc, (FileExistsError, PermissionError)):
            code = ErrorCode.CONFLICT
        elif isinstance(exc, OSError):
            code = ErrorCode.STORAGE_UNAVAILABLE
        else:
            code = ErrorCode.INVALID_INPUT
        return OperationResult.failure(
            code,
            str(exc) or exc.__class__.__name__,
            retryable=code is ErrorCode.STORAGE_UNAVAILABLE,
        )

    def _entry(
        self,
        object_id: LogicalObjectId,
        *,
        include_deleted: bool = False,
    ) -> OperationResult[CatalogEntry]:
        result = self.catalog.get(object_id, include_deleted=include_deleted)
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

    @staticmethod
    def _matches(entry: CatalogEntry, expected: str | None) -> bool:
        return expected is None or str(expected) in {entry.version_id, entry.content_sha256}

    def _discard_new(self, stored: StoredObject) -> bool:
        current = self.catalog.get(stored.object_id)
        if not current.ok:
            return False
        result = self.catalog.mark_deleted(
            stored.object_id,
            expected_version_id=current.value.version_id,
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

    def _projection_error(self, object_id: LogicalObjectId, exc: Exception, message: str):
        self.catalog.mark_recovery(object_id)
        failure = self._projection_failure(exc)
        return self._error(
            ErrorCode.STORAGE_UNAVAILABLE if failure.error and failure.error.code is ErrorCode.STORAGE_UNAVAILABLE else ErrorCode.INTEGRITY_ERROR,
            message,
            retryable=bool(failure.error and failure.error.retryable),
        )

    def read_bytes(self, object_id: LogicalObjectId) -> OperationResult[bytes]:
        return self.primary.read_bytes(object_id)

    def read_version_bytes(self, object_id: LogicalObjectId, version_id: str) -> OperationResult[bytes]:
        try:
            payload = self.primary.blobs.read(object_id, version_id=str(version_id))
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            return self._projection_failure(exc)
        return OperationResult.success(payload)

    def copy_verified_to(
        self,
        object_id: LogicalObjectId,
        target: BinaryIO,
    ) -> OperationResult[StoredObject]:
        streamed = self.primary.copy_verified_to(object_id, target)
        if not streamed.ok:
            return OperationResult(error=streamed.error)
        return self._compat_result(object_id)

    def copy_verified_range_to(
        self,
        object_id: LogicalObjectId,
        target: BinaryIO,
        *,
        start: int,
        length: int | None = None,
    ) -> OperationResult[StoredObject]:
        streamed = self.primary.copy_verified_range_to(
            object_id,
            target,
            start=start,
            length=length,
        )
        if not streamed.ok:
            return OperationResult(error=streamed.error)
        return self._compat_result(object_id)

    def create_bytes(
        self,
        location: StorageLocation,
        content: bytes,
    ) -> OperationResult[StoredObject]:
        primary = self.primary.create_bytes(location, bytes(content))
        if not primary.ok:
            return OperationResult(error=primary.error)
        entry = self.catalog.get(primary.value.object_id)
        if not entry.ok:
            return OperationResult(error=entry.error)
        try:
            self.projection.create(entry.value)
        except (OSError, RuntimeError, ValueError) as exc:
            if not self._discard_new(primary.value):
                self.catalog.mark_recovery(primary.value.object_id)
            return self._projection_failure(exc)
        return self._compat_result(primary.value.object_id)

    def import_stream(
        self,
        stream: BinaryIO,
        filename: str,
        *,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        primary = self.primary.import_stream(
            stream,
            filename,
            archive=archive,
            max_bytes=max_bytes,
        )
        if not primary.ok:
            return OperationResult(error=primary.error)
        entry = self.catalog.get(primary.value.object_id)
        if not entry.ok:
            return OperationResult(error=entry.error)
        try:
            parent = self.root / Path(entry.value.location.relative_path).parent
            parent.mkdir(parents=True, exist_ok=True)
            self.store.ensure_folder_policy(parent, self.actor)
            self.projection.create(entry.value)
        except (OSError, RuntimeError, ValueError) as exc:
            if not self._discard_new(primary.value):
                self.catalog.mark_recovery(primary.value.object_id)
            return self._projection_failure(exc)
        return self._compat_result(primary.value.object_id)

    def replace_bytes(
        self,
        object_id: LogicalObjectId,
        content: bytes,
        *,
        expected_version: str | None = None,
        source: str = "v2-authoritative",
        restored_from_version: str = "",
    ) -> OperationResult[StoredObject]:
        current = self._entry(object_id)
        if not current.ok:
            return OperationResult(error=current.error)
        previous = current.value
        if not self._matches(previous, expected_version):
            return self._error(ErrorCode.CONFLICT, "document content changed since it was opened")
        primary = self.primary.replace_bytes(
            object_id,
            bytes(content),
            expected_version=previous.version_id,
            source=source,
            restored_from_version=restored_from_version,
        )
        if not primary.ok:
            return OperationResult(error=primary.error)
        updated = self._entry(object_id)
        if not updated.ok:
            return OperationResult(error=updated.error)
        try:
            self.projection.replace(previous, updated.value, source=source)
        except (OSError, RuntimeError, ValueError) as exc:
            if not self._restore_content(previous, updated.value.version_id):
                self.catalog.mark_recovery(object_id)
            return self._projection_failure(exc)
        return self._compat_result(object_id)

    def copy(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        source = self._entry(object_id)
        if not source.ok:
            return OperationResult(error=source.error)
        primary = self.primary.copy(object_id, destination)
        if not primary.ok:
            return OperationResult(error=primary.error)
        created = self._entry(primary.value.object_id)
        if not created.ok:
            return OperationResult(error=created.error)
        try:
            self.projection.copy(source.value, created.value)
        except (OSError, RuntimeError, ValueError) as exc:
            if not self._discard_new(primary.value):
                self.catalog.mark_recovery(primary.value.object_id)
            return self._projection_failure(exc)
        return self._compat_result(primary.value.object_id)

    def copy_replace(
        self,
        source_id: LogicalObjectId,
        destination_id: LogicalObjectId,
        *,
        expected_source_version: str,
        expected_destination_version: str,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        source = self._entry(source_id)
        destination = self._entry(destination_id)
        if not source.ok:
            return OperationResult(error=source.error)
        if not destination.ok:
            return OperationResult(error=destination.error)
        primary = self.primary.copy_replace(
            source_id,
            destination_id,
            expected_source_version=expected_source_version,
            expected_destination_version=expected_destination_version,
            max_bytes=max_bytes,
        )
        if not primary.ok:
            return OperationResult(error=primary.error)
        current = self._entry(destination_id)
        if not current.ok:
            return OperationResult(error=current.error)
        try:
            self.projection.copy_replace(source.value, destination.value, current.value)
        except (OSError, RuntimeError, ValueError) as exc:
            if not self._restore_content(destination.value, current.value.version_id):
                self.catalog.mark_recovery(destination_id)
            return self._projection_failure(exc)
        return self._compat_result(destination_id)

    def move(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        current = self._entry(object_id)
        if not current.ok:
            return OperationResult(error=current.error)
        previous = current.value
        primary = self.primary.move(object_id, destination)
        if not primary.ok:
            return OperationResult(error=primary.error)
        moved = self._entry(object_id)
        if not moved.ok:
            return OperationResult(error=moved.error)
        try:
            self.projection.move(previous, moved.value)
        except (OSError, RuntimeError, ValueError) as exc:
            rollback = self.catalog.move(
                object_id,
                previous.location,
                expected_version_id=moved.value.version_id,
            )
            if not rollback.ok:
                self.catalog.mark_recovery(object_id)
            return self._projection_failure(exc)
        return self._compat_result(object_id)

    def move_replace(
        self,
        source_id: LogicalObjectId,
        destination_id: LogicalObjectId,
        *,
        expected_source_version: str,
        expected_destination_version: str,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        source = self._entry(source_id)
        destination = self._entry(destination_id)
        if not source.ok:
            return OperationResult(error=source.error)
        if not destination.ok:
            return OperationResult(error=destination.error)
        primary = self.primary.move_replace(
            source_id,
            destination_id,
            expected_source_version=expected_source_version,
            expected_destination_version=expected_destination_version,
            max_bytes=max_bytes,
        )
        if not primary.ok:
            return OperationResult(error=primary.error)
        deleted_source = self._entry(source_id, include_deleted=True)
        current_destination = self._entry(destination_id)
        if not deleted_source.ok:
            return OperationResult(error=deleted_source.error)
        if not current_destination.ok:
            return OperationResult(error=current_destination.error)
        try:
            self.projection.move_replace(
                source.value,
                deleted_source.value,
                destination.value,
                current_destination.value,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            destination_rollback = self._restore_content(
                destination.value,
                current_destination.value.version_id,
            )
            source_rollback = self.catalog.restore(source_id, location=source.value.location)
            if not destination_rollback or not source_rollback.ok:
                self.catalog.mark_recovery(source_id)
                self.catalog.mark_recovery(destination_id)
            return self._projection_failure(exc)
        return self._compat_result(destination_id)

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
        if not self._matches(previous, expected_version):
            return self._error(ErrorCode.CONFLICT, "document content changed since it was opened")
        primary = self.primary.delete(object_id, expected_version=previous.version_id)
        if not primary.ok:
            return OperationResult(error=primary.error)
        deleted = self._entry(object_id, include_deleted=True)
        if not deleted.ok:
            return OperationResult(error=deleted.error)
        try:
            self.projection.delete(previous, deleted.value)
        except (OSError, RuntimeError, ValueError) as exc:
            rollback = self.catalog.restore(object_id, location=previous.location)
            if not rollback.ok:
                self.catalog.mark_recovery(object_id)
            return self._projection_failure(exc)
        return OperationResult.success(previous.content_sha256)

    def restore(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[StoredObject]:
        current = self._entry(object_id, include_deleted=True)
        if not current.ok:
            return OperationResult(error=current.error)
        previous = current.value
        if previous.state.value != "deleted":
            return self._error(ErrorCode.CONFLICT, "catalog object is not deleted")
        if not self._matches(previous, expected_version):
            return self._error(ErrorCode.CONFLICT, "catalog object version changed")
        primary = self.primary.restore(
            object_id,
            destination,
            expected_version=previous.version_id,
        )
        if not primary.ok:
            return OperationResult(error=primary.error)
        restored = self._entry(object_id)
        if not restored.ok:
            return OperationResult(error=restored.error)
        try:
            self.projection.restore(previous, restored.value)
        except (OSError, RuntimeError, ValueError) as exc:
            rollback = self.catalog.mark_deleted(
                object_id,
                expected_version_id=restored.value.version_id,
            )
            if not rollback.ok:
                self.catalog.mark_recovery(object_id)
            return self._projection_failure(exc)
        return self._compat_result(object_id)
