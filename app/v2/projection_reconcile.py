"""Reconcile out-of-band filesystem watcher observations into V2 storage.

DocumentStore remains the compatibility projection. The index worker first
updates that projection, then this service adopts the verified observation into
the V2 blob/catalog authority. It is deliberately not part of normal web/VFS
mutations, which already cross StoragePort directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.document_store import (
    CONTROL_DIR,
    HISTORY_DIR,
    PREVIEW_CACHE_DIR,
    DocumentStore,
)

from .adapters.authoritative import V2AuthoritativeStorageAdapter
from .adapters.blob_catalog import BlobCatalogStorageAdapter
from .adapters.shadow import ShadowDocumentStorageAdapter
from .contracts import ErrorCode, LogicalObjectId, StorageLocation
from .cutover import load_cutover_state, mark_storage_dirty
from .storage_runtime import storage_for


V2_CONTROL_DIR = ".simpleoffice-v2"


@dataclass(frozen=True)
class ProjectionReconcileReport:
    examined: int = 0
    reconciled: int = 0
    deleted: int = 0
    skipped: int = 0
    recovery_needed: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "examined": self.examined,
            "reconciled": self.reconciled,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "recovery_needed": self.recovery_needed,
        }


def _relative(root: Path, value: str | Path) -> str:
    candidate = Path(value).resolve(strict=False)
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        return ""
    if not relative:
        return ""
    if relative.split("/", 1)[0] in {
        CONTROL_DIR,
        HISTORY_DIR,
        PREVIEW_CACHE_DIR,
        V2_CONTROL_DIR,
    }:
        return ""
    return relative


def _primary(runtime) -> BlobCatalogStorageAdapter | None:
    if isinstance(runtime, V2AuthoritativeStorageAdapter):
        return runtime.primary
    if isinstance(runtime, ShadowDocumentStorageAdapter):
        return BlobCatalogStorageAdapter(
            runtime.root,
            runtime.actor,
            blob_store=runtime.blobs,
            catalog=runtime.catalog,
        )
    return None


def _failed(root: Path, object_id: str, reason: str, *, primary=None) -> None:
    mark_storage_dirty(root, reason)
    if primary is None or not object_id:
        return
    try:
        current = primary.catalog.get(LogicalObjectId(object_id))
        if current.ok:
            primary.catalog.mark_recovery(current.value.object_id)
    except (OSError, RuntimeError, TypeError, ValueError):
        return


def reconcile_changed_paths(
    root: str | Path,
    paths: Iterable[str | Path],
    *,
    actor: str = "filesystem-watcher",
) -> ProjectionReconcileReport:
    """Adopt already-scanned external file changes into shadow/V2 storage.

    V1 mode intentionally remains unchanged. A failed V2 reconciliation never
    guesses: it persists dirty/recovery state so cutover/cleanup cannot claim a
    clean store.
    """

    data_root = Path(root).expanduser().resolve()
    state = load_cutover_state(data_root)
    if state.mode not in {"shadow", "v2"}:
        return ProjectionReconcileReport()

    try:
        runtime = storage_for(data_root, actor)
    except (OSError, RuntimeError, TypeError, ValueError):
        mark_storage_dirty(
            data_root,
            "filesystem watcher could not open the configured V2 storage backend",
        )
        return ProjectionReconcileReport(
            examined=0,
            recovery_needed=1,
        )

    primary = _primary(runtime)
    if primary is None:
        return ProjectionReconcileReport()

    documents = DocumentStore(data_root)
    unique = sorted(
        {
            relative
            for value in paths
            if (relative := _relative(data_root, value))
        }
    )
    examined = reconciled = deleted = skipped = recovery_needed = 0

    for relative in unique:
        examined += 1
        path = data_root / relative
        if path.is_file() and not path.is_symlink():
            object_id = ""
            try:
                metadata = documents.get_document(path)
                object_id = str(metadata.get("document_id") or "")
                digest = str(metadata.get("sha256") or "").casefold()
                if not object_id or len(digest) != 64:
                    raise ValueError("scanned document metadata is incomplete")
                size = path.stat().st_size
                with path.open("rb") as handle:
                    result = primary.reconcile_external_stream(
                        LogicalObjectId(object_id),
                        StorageLocation(relative),
                        handle,
                        expected_size=size,
                        expected_sha256=digest,
                    )
                if not result.ok:
                    code = result.error.code if result.error else ErrorCode.INTERNAL_ERROR
                    raise RuntimeError(f"external projection reconciliation failed: {code.value}")
                reconciled += 1
            except (OSError, RuntimeError, TypeError, ValueError):
                recovery_needed += 1
                _failed(
                    data_root,
                    object_id,
                    f"filesystem watcher could not reconcile {object_id or 'new object'}",
                    primary=primary,
                )
            continue

        if path.exists():
            skipped += 1
            continue

        lookup = primary.catalog.get_by_location(StorageLocation(relative))
        if not lookup.ok:
            skipped += 1
            continue
        entry = lookup.value
        object_id = entry.object_id.value
        try:
            metadata = documents.get_document(object_id)
            observed_location = str(metadata.get("last_path") or "").strip()
            if observed_location and observed_location != relative:
                moved = data_root / observed_location
                if moved.is_file() and not moved.is_symlink():
                    # The same watcher batch already recognized a rename/move.
                    skipped += 1
                    continue

            content = primary.read_bytes(entry.object_id)
            if not content.ok or content.value is None:
                raise RuntimeError("authoritative V2 content is unavailable")
            removed = primary.delete(
                entry.object_id,
                expected_version=entry.version_id,
            )
            if not removed.ok:
                raise RuntimeError("V2 catalog delete failed")
            try:
                documents.capture_external_deletion(
                    object_id,
                    actor,
                    content.value,
                    expected_sha256=entry.content_sha256,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                restored = primary.catalog.restore(
                    entry.object_id,
                    location=entry.location,
                )
                if restored.ok:
                    primary.catalog.mark_recovery(entry.object_id)
                raise
            deleted += 1
        except (OSError, RuntimeError, TypeError, ValueError):
            recovery_needed += 1
            _failed(
                data_root,
                object_id,
                f"filesystem watcher could not preserve recovery for {object_id}",
                primary=primary,
            )

    return ProjectionReconcileReport(
        examined=examined,
        reconciled=reconciled,
        deleted=deleted,
        skipped=skipped,
        recovery_needed=recovery_needed,
    )
