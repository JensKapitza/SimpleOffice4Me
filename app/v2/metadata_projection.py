"""Metadata-only projection for authoritative V2 document storage.

The projection preserves the existing UI/search/policy metadata model without
writing document payload bytes into the presentation filesystem. Object content
and lifecycle state remain authoritative in ObjectCatalog/BlobStore.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from app.document_store import DocumentStore, utc_now

from .catalog import CatalogEntry, CatalogState
from .contracts import LogicalObjectId, StorageLocation


class V2MetadataProjection:
    def __init__(self, root: str | Path, actor: str):
        self.root = Path(root).expanduser().resolve()
        self.actor = str(actor or "").strip()
        if not self.actor:
            raise ValueError("metadata projection requires an actor")
        self.store = DocumentStore(self.root)
        self.store.initialize()

    def _ensure_parent(self, location: StorageLocation) -> None:
        relative = self.store._safe_managed_relative_path(
            location.relative_path,
            require_name=True,
        )
        parent = self.root / relative.parent
        if not parent.is_dir() or parent.is_symlink():
            raise ValueError("destination collection does not exist")
        self.store.ensure_folder_policy(parent, self.actor)

    def _read(self, object_id: LogicalObjectId) -> dict[str, Any]:
        return self.store.get_document(object_id.value)

    def _persist(self, metadata: dict[str, Any], entry: CatalogEntry) -> dict[str, Any]:
        metadata = copy.deepcopy(metadata)
        metadata["document_id"] = entry.object_id.value
        metadata["sha256"] = entry.content_sha256
        metadata["content_sha256"] = entry.content_sha256
        metadata["last_path"] = entry.location.relative_path if entry.state is CatalogState.ACTIVE else ""
        metadata["last_seen_at"] = utc_now()
        metadata["system_state"] = (
            "indexed" if entry.state is CatalogState.ACTIVE else
            "webdav_deleted" if entry.state is CatalogState.DELETED else
            "recovery"
        )
        metadata.setdefault("version", 1)
        metadata.setdefault("first_seen_at", metadata["last_seen_at"])
        metadata.setdefault("original_sha256", entry.content_sha256)
        metadata.setdefault("tags", [])
        metadata.setdefault("tagged_at", {})
        metadata.setdefault("notes", [])
        metadata.setdefault("relationships", [])
        metadata.setdefault("state", "new")
        metadata.setdefault("state_history", [])
        metadata.setdefault("version_series_id", entry.object_id.value)
        metadata.setdefault("version_number", 1)
        metadata.setdefault("attributes", {})
        metadata.setdefault("deadlines", [])
        self.store._save_document(metadata)
        self.store._refresh_search_index(metadata)
        self._sync_scan_row(metadata, entry)
        return metadata

    def _sync_scan_row(self, metadata: dict[str, Any], entry: CatalogEntry) -> None:
        with self.store._db() as db:
            db.execute(
                "DELETE FROM scan_file WHERE document_id = ?",
                (entry.object_id.value,),
            )
            if entry.state is CatalogState.ACTIVE:
                db.execute(
                    """INSERT OR REPLACE INTO scan_file(
                           relative_path, document_id, sha256, size, modified_ns,
                           device, inode, last_seen_at
                       ) VALUES (?, ?, ?, ?, 0, NULL, NULL, ?)""",
                    (
                        entry.location.relative_path,
                        entry.object_id.value,
                        entry.content_sha256,
                        entry.size,
                        str(metadata.get("last_seen_at") or utc_now()),
                    ),
                )

    @staticmethod
    def _version_record(entry: CatalogEntry) -> dict[str, Any]:
        return {
            "version_id": entry.version_id,
            "sha256": entry.content_sha256,
            "size": entry.size,
            "archived_at": utc_now(),
        }

    def create(self, entry: CatalogEntry) -> dict[str, Any]:
        self._ensure_parent(entry.location)
        name = Path(entry.location.relative_path).name
        metadata: dict[str, Any] = {
            "document_id": entry.object_id.value,
            "version": 1,
            "first_seen_at": utc_now(),
            "last_seen_at": utc_now(),
            "last_path": entry.location.relative_path,
            "sha256": entry.content_sha256,
            "original_sha256": entry.content_sha256,
            "content_sha256": entry.content_sha256,
            "tags": sorted(self.store._filename_tags(name), key=str.casefold),
            "tagged_at": {},
            "notes": [],
            "relationships": [],
            "state": "new",
            "state_history": [],
            "version_series_id": entry.object_id.value,
            "version_number": 1,
            "attributes": {},
            "deadlines": [],
            "system_state": "indexed",
            "v2_content_versions": [],
        }
        result = self._persist(metadata, entry)
        self.store._event(
            "v2_metadata_created",
            {"document_id": entry.object_id.value, "path": entry.location.relative_path, "actor": self.actor},
        )
        self.store._record_revision(
            "v2_metadata_created", self.actor, "documents", entry.object_id.value, result
        )
        return result

    def replace(self, previous: CatalogEntry, current: CatalogEntry, *, source: str = "") -> dict[str, Any]:
        metadata = self._read(previous.object_id)
        versions = list(metadata.get("v2_content_versions") or [])
        record = self._version_record(previous)
        if not any(str(row.get("version_id")) == previous.version_id for row in versions if isinstance(row, dict)):
            versions.append(record)
        metadata["v2_content_versions"] = versions[-200:]
        metadata["version_number"] = int(metadata.get("version_number", 1) or 1) + 1
        metadata.setdefault("content_history", []).append({
            "from_sha256": previous.content_sha256,
            "to_sha256": current.content_sha256,
            "from_version_id": previous.version_id,
            "to_version_id": current.version_id,
            "source": str(source or "v2-storage"),
            "at": utc_now(),
            "actor": self.actor,
        })
        metadata["content_history"] = metadata["content_history"][-200:]
        result = self._persist(metadata, current)
        self.store._event(
            "v2_metadata_content_replaced",
            {"document_id": current.object_id.value, "actor": self.actor, "source": str(source or "v2-storage")},
        )
        self.store._record_revision(
            "v2_metadata_content_replaced", self.actor, "documents", current.object_id.value, result
        )
        return result

    def copy(self, source: CatalogEntry, current: CatalogEntry) -> dict[str, Any]:
        self._ensure_parent(current.location)
        source_metadata = self._read(source.object_id)
        metadata = copy.deepcopy(source_metadata)
        metadata["document_id"] = current.object_id.value
        metadata["first_seen_at"] = utc_now()
        metadata["version_series_id"] = current.object_id.value
        metadata["version_number"] = 1
        metadata["v2_content_versions"] = []
        metadata.pop("deleted_at", None)
        metadata.pop("deleted_by", None)
        metadata.pop("deleted_from", None)
        metadata.pop("recovery_path", None)
        result = self._persist(metadata, current)
        self.store._event(
            "v2_metadata_copied",
            {"document_id": current.object_id.value, "copied_from": source.object_id.value, "actor": self.actor},
        )
        self.store._record_revision(
            "v2_metadata_copied", self.actor, "documents", current.object_id.value, result
        )
        return result

    def move(self, previous: CatalogEntry, current: CatalogEntry) -> dict[str, Any]:
        self._ensure_parent(current.location)
        metadata = self._read(previous.object_id)
        metadata.setdefault("location_history", []).append({
            "from": previous.location.relative_path,
            "to": current.location.relative_path,
            "at": utc_now(),
            "actor": self.actor,
        })
        metadata["location_history"] = metadata["location_history"][-200:]
        result = self._persist(metadata, current)
        self.store._event(
            "v2_metadata_moved",
            {"document_id": current.object_id.value, "from": previous.location.relative_path, "to": current.location.relative_path, "actor": self.actor},
        )
        self.store._record_revision(
            "v2_metadata_moved", self.actor, "documents", current.object_id.value, result
        )
        return result

    def delete(self, previous: CatalogEntry, deleted: CatalogEntry) -> dict[str, Any]:
        metadata = self._read(previous.object_id)
        deleted_at = utc_now()
        metadata["deleted_at"] = deleted_at
        metadata["deleted_by"] = self.actor
        metadata["deleted_from"] = previous.location.relative_path
        metadata.pop("recovery_path", None)
        metadata.setdefault("location_history", []).append({
            "from": previous.location.relative_path,
            "to": "[v2-recovery]",
            "at": deleted_at,
            "actor": self.actor,
        })
        metadata["location_history"] = metadata["location_history"][-200:]
        result = self._persist(metadata, deleted)
        self.store._event(
            "v2_metadata_deleted",
            {"document_id": deleted.object_id.value, "from": previous.location.relative_path, "actor": self.actor},
        )
        self.store._record_revision(
            "v2_metadata_deleted", self.actor, "documents", deleted.object_id.value, result
        )
        return result

    def restore(self, previous: CatalogEntry, current: CatalogEntry) -> dict[str, Any]:
        self._ensure_parent(current.location)
        metadata = self._read(previous.object_id)
        metadata.setdefault("recovery_history", []).append({
            "deleted_at": metadata.get("deleted_at", ""),
            "deleted_by": metadata.get("deleted_by", ""),
            "restored_at": utc_now(),
            "restored_by": self.actor,
            "destination": current.location.relative_path,
            "sha256": current.content_sha256,
        })
        metadata["recovery_history"] = metadata["recovery_history"][-200:]
        metadata.pop("deleted_at", None)
        metadata.pop("deleted_by", None)
        metadata.pop("deleted_from", None)
        metadata.pop("recovery_path", None)
        result = self._persist(metadata, current)
        self.store._event(
            "v2_metadata_restored",
            {"document_id": current.object_id.value, "to": current.location.relative_path, "actor": self.actor},
        )
        self.store._record_revision(
            "v2_metadata_restored", self.actor, "documents", current.object_id.value, result
        )
        return result

    def copy_replace(self, source: CatalogEntry, previous_destination: CatalogEntry, current_destination: CatalogEntry) -> dict[str, Any]:
        return self.replace(previous_destination, current_destination, source=f"copy:{source.object_id.value}")

    def move_replace(
        self,
        source: CatalogEntry,
        deleted_source: CatalogEntry,
        previous_destination: CatalogEntry,
        current_destination: CatalogEntry,
    ) -> dict[str, Any]:
        result = self.copy_replace(source, previous_destination, current_destination)
        self.delete(source, deleted_source)
        return result
