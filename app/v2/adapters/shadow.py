"""Shadow adapter that keeps verified V2 storage synchronized with V1.

V1 remains the user-visible compatibility source in shadow mode. Mutations are
committed through the existing DocumentStore adapter first, then mirrored to the
BlobStore/ObjectCatalog with the exact same logical object ID and location.
Mirror failures never hide a successful V1 mutation; instead the persistent
cutover state is marked dirty so final activation stays blocked.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..blob_store import BlobStore
from ..catalog import CatalogState, ObjectCatalog
from ..contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation, StoredObject
from ..cutover import mark_shadow_dirty
from .document_store import DocumentStoreStorageAdapter


class ShadowDocumentStorageAdapter:
    def __init__(self, root: str | Path, actor: str):
        self.root = Path(root).expanduser().resolve()
        self.actor = str(actor or "").strip()
        self.legacy = DocumentStoreStorageAdapter(self.root, self.actor)
        self.blobs = BlobStore(self.root)
        self.catalog = ObjectCatalog(self.root)

    def _dirty(self, reason: str) -> None:
        mark_shadow_dirty(self.root, reason)

    @staticmethod
    def _message(result: OperationResult) -> str:
        return result.error.message if result.error else "unknown V2 shadow error"

    def _ensure_active(self, stored: StoredObject, content: bytes) -> None:
        object_id = stored.object_id
        digest = hashlib.sha256(content).hexdigest()
        if digest != stored.version:
            self._dirty(f"{object_id.value}: V1 integrity metadata changed during shadow mirror")
            return
        try:
            version = self.blobs.write(object_id, content)
        except (OSError, RuntimeError, ValueError) as exc:
            self._dirty(f"{object_id.value}: BlobStore mirror failed: {exc}")
            return

        existing = self.catalog.get(object_id, include_deleted=True)
        if not existing.ok:
            if existing.error and existing.error.code is not ErrorCode.NOT_FOUND:
                self._dirty(f"{object_id.value}: catalog lookup failed: {self._message(existing)}")
                return
            registered = self.catalog.register(
                object_id,
                stored.location,
                version_id=version.version_id,
                size=version.size,
                content_sha256=version.content_sha256,
            )
            if not registered.ok:
                self._dirty(f"{object_id.value}: catalog registration failed: {self._message(registered)}")
            return

        row = existing.value
        if row.state is CatalogState.DELETED:
            restored = self.catalog.restore(object_id, location=stored.location)
            if not restored.ok:
                self._dirty(f"{object_id.value}: catalog restore failed: {self._message(restored)}")
                return
            row = restored.value
        elif row.state is CatalogState.RECOVERY:
            active = self.catalog.mark_active(object_id)
            if not active.ok:
                self._dirty(f"{object_id.value}: catalog activation failed: {self._message(active)}")
                return
            row = active.value

        if row.location != stored.location:
            moved = self.catalog.move(
                object_id,
                stored.location,
                expected_version_id=row.version_id,
            )
            if not moved.ok:
                self._dirty(f"{object_id.value}: catalog move failed: {self._message(moved)}")
                return
            row = moved.value

        updated = self.catalog.update_content(
            object_id,
            version_id=version.version_id,
            size=version.size,
            content_sha256=version.content_sha256,
            expected_version_id=row.version_id,
        )
        if not updated.ok:
            self._dirty(f"{object_id.value}: catalog content mirror failed: {self._message(updated)}")

    def _mirror_result(self, result: OperationResult[StoredObject]) -> OperationResult[StoredObject]:
        if not result.ok:
            return result
        content = self.legacy.read_bytes(result.value.object_id)
        if not content.ok:
            self._dirty(
                f"{result.value.object_id.value}: V1 read-back failed after mutation: "
                f"{self._message(content)}"
            )
            return result
        self._ensure_active(result.value, content.value)
        return result

    def _compare_read(self, object_id: LogicalObjectId, legacy: OperationResult[bytes]) -> None:
        if not legacy.ok:
            return
        row = self.catalog.get(object_id)
        if not row.ok:
            self._dirty(f"{object_id.value}: V2 shadow catalog entry is missing")
            return
        try:
            content = self.blobs.read(object_id, version_id=row.value.version_id)
        except (OSError, RuntimeError, ValueError) as exc:
            self._dirty(f"{object_id.value}: V2 shadow read failed: {exc}")
            return
        if content != legacy.value:
            self._dirty(f"{object_id.value}: V1/V2 shadow content mismatch")
            return
        digest = hashlib.sha256(content).hexdigest()
        if (
            row.value.location.relative_path == ""
            or row.value.size != len(content)
            or row.value.content_sha256 != digest
        ):
            self._dirty(f"{object_id.value}: V2 shadow catalog metadata mismatch")

    def read_bytes(self, object_id: LogicalObjectId) -> OperationResult[bytes]:
        result = self.legacy.read_bytes(object_id)
        self._compare_read(object_id, result)
        return result

    def create_bytes(self, location: StorageLocation, content: bytes) -> OperationResult[StoredObject]:
        return self._mirror_result(self.legacy.create_bytes(location, content))

    def import_stream(
        self,
        stream,
        filename: str,
        *,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        return self._mirror_result(
            self.legacy.import_stream(
                stream,
                filename,
                archive=archive,
                max_bytes=max_bytes,
            )
        )

    def replace_bytes(
        self,
        object_id: LogicalObjectId,
        content: bytes,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[StoredObject]:
        return self._mirror_result(
            self.legacy.replace_bytes(
                object_id,
                content,
                expected_version=expected_version,
            )
        )

    def copy(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        return self._mirror_result(self.legacy.copy(object_id, destination))

    def move(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
    ) -> OperationResult[StoredObject]:
        result = self.legacy.move(object_id, destination)
        if not result.ok:
            return result
        row = self.catalog.get(object_id)
        if not row.ok:
            return self._mirror_result(result)
        moved = self.catalog.move(
            object_id,
            result.value.location,
            expected_version_id=row.value.version_id,
        )
        if not moved.ok:
            self._dirty(f"{object_id.value}: V2 shadow move failed: {self._message(moved)}")
        return result

    def delete(
        self,
        object_id: LogicalObjectId,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[str]:
        result = self.legacy.delete(object_id, expected_version=expected_version)
        if not result.ok:
            return result
        row = self.catalog.get(object_id)
        if not row.ok:
            self._dirty(f"{object_id.value}: V2 shadow delete has no catalog entry")
            return result
        deleted = self.catalog.mark_deleted(
            object_id,
            expected_version_id=row.value.version_id,
        )
        if not deleted.ok:
            self._dirty(f"{object_id.value}: V2 shadow delete failed: {self._message(deleted)}")
        return result
