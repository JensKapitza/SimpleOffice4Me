"""V2 StoragePort adapter over the existing V1 DocumentStore.

This adapter is deliberately transitional: it preserves all existing V1 files,
metadata, version archives, audit hooks and recovery behavior while presenting
the V2 logical-object contract to new application services.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.document_store import DocumentStore
from app.safe_paths import resolve_file_under

from ..contracts import (
    ErrorCode,
    LogicalObjectId,
    OperationResult,
    StorageLocation,
    StoredObject,
)


class DocumentStoreStorageAdapter:
    def __init__(self, root: str | Path, actor: str):
        self.store = DocumentStore(root)
        self.actor = str(actor or "").strip()
        if not self.actor:
            raise ValueError("storage adapter requires an actor")

    @staticmethod
    def _stored(metadata: dict) -> StoredObject:
        return StoredObject(
            object_id=LogicalObjectId(str(metadata["document_id"])),
            version=str(metadata.get("sha256") or metadata.get("content_sha256") or ""),
            size=int(metadata.get("size") or 0),
            location=StorageLocation(str(metadata.get("last_path") or "")),
        )

    @staticmethod
    def _failure(exc: Exception):
        message = str(exc)
        lowered = message.casefold()
        if isinstance(exc, FileExistsError):
            code = ErrorCode.CONFLICT
        elif isinstance(exc, FileNotFoundError) or "unknown document" in lowered or "unavailable" in lowered:
            code = ErrorCode.NOT_FOUND
        elif isinstance(exc, RuntimeError) and ("verified" in lowered or "integrity" in lowered):
            code = ErrorCode.INTEGRITY_ERROR
        elif "changed since" in lowered or "already exists" in lowered:
            code = ErrorCode.CONFLICT
        elif isinstance(exc, OSError):
            code = ErrorCode.STORAGE_UNAVAILABLE
        else:
            code = ErrorCode.INVALID_INPUT
        return OperationResult.failure(
            code,
            message,
            retryable=code in {ErrorCode.STORAGE_UNAVAILABLE, ErrorCode.RETRYABLE},
        )

    def read_bytes(self, object_id: LogicalObjectId) -> OperationResult[bytes]:
        try:
            metadata = self.store.get_document(object_id.value)
            if metadata.get("system_state") == "webdav_deleted" or metadata.get("deleted_at"):
                return OperationResult.failure(
                    ErrorCode.NOT_FOUND,
                    "document is deleted",
                )
            path = resolve_file_under(self.store.root, str(metadata.get("last_path") or ""))
            content = path.read_bytes()
            expected = str(metadata.get("sha256") or "")
            if expected and hashlib.sha256(content).hexdigest() != expected:
                return OperationResult.failure(
                    ErrorCode.INTEGRITY_ERROR,
                    "document content does not match stored integrity metadata",
                )
            return OperationResult.success(content)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc)

    def create_bytes(self, location: StorageLocation, content: bytes) -> OperationResult[StoredObject]:
        try:
            metadata = self.store.create_document_at(
                location.relative_path,
                bytes(content),
                self.actor,
            )
            return OperationResult.success(self._stored(metadata))
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc)

    def import_stream(
        self,
        stream,
        filename: str,
        *,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        try:
            metadata = self.store.import_upload(
                stream,
                filename,
                self.actor,
                archive=bool(archive),
                max_bytes=int(max_bytes),
            )
            return OperationResult.success(self._stored(metadata))
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc)

    def replace_bytes(
        self,
        object_id: LogicalObjectId,
        content: bytes,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[StoredObject]:
        try:
            metadata = self.store.replace_content(
                object_id.value,
                bytes(content),
                self.actor,
                expected_sha256=str(expected_version or ""),
                source="v2-storage-adapter",
            )
            return OperationResult.success(self._stored(metadata))
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc)

    def copy(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        try:
            metadata = self.store.copy_document(
                object_id.value,
                destination.relative_path,
                self.actor,
            )
            stored = self._stored(metadata)
            if stored.object_id == object_id:
                return OperationResult.failure(
                    ErrorCode.INTEGRITY_ERROR,
                    "copied document reused the source identity",
                )
            return OperationResult.success(stored)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc)

    def delete(
        self,
        object_id: LogicalObjectId,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[str]:
        try:
            metadata = self.store.get_document(object_id.value)
            version = str(metadata.get("sha256") or "")
            self.store.soft_delete_document(
                object_id.value,
                self.actor,
                expected_sha256=str(expected_version or ""),
            )
            return OperationResult.success(version)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc)

    def move(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        try:
            target = Path(destination.relative_path)
            metadata = self.store.move_document(
                object_id.value,
                target.parent.as_posix() if target.parent.as_posix() != "." else "",
                self.actor,
                destination_name=target.name,
            )
            stored = self._stored(metadata)
            if stored.object_id != object_id:
                return OperationResult.failure(
                    ErrorCode.INTEGRITY_ERROR,
                    "document identity changed during move",
                )
            return OperationResult.success(stored)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc)
