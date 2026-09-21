"""Read-only V1 -> V2 migration preflight.

This module never mutates an installation. It inventories the legacy stores and
reports blockers before a future migration is allowed to run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .blob_store import BlobStore
from .contracts import LogicalObjectId


@dataclass(frozen=True)
class MigrationPreflight:
    root: str
    document_root_exists: bool
    v2_inventory_readable: bool
    v2_invalid_objects: int
    legacy_history_present: bool
    legacy_control_present: bool
    ready: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_migration(root: str | Path) -> MigrationPreflight:
    path = Path(root).expanduser().resolve()
    blockers: list[str] = []
    exists = path.is_dir()
    if not exists:
        blockers.append("document root does not exist")

    invalid = 0
    inventory_readable = False
    if exists:
        base = path / ".simpleoffice-v2" / "blob-store"
        versions = base / "versions"
        objects = base / "objects"
        chunks = base / "chunks"
        staging = base / "staging"
        if not base.exists():
            inventory_readable = True
        elif not all(item.is_dir() for item in (versions, objects, chunks, staging)):
            blockers.append("V2 recovery inventory is not readable")
        else:
            try:
                # BlobStore construction is safe here because all directories
                # already exist; never construct it for a legacy-only root.
                store = BlobStore(path)
                store.inventory()
                for manifest_path in sorted(versions.glob("*.json")):
                    try:
                        manifest = store.version_manifest(manifest_path.stem)
                        store.read(LogicalObjectId(str(manifest["object_id"])), version_id=manifest_path.stem)
                    except (OSError, ValueError, TypeError, KeyError):
                        invalid += 1
                inventory_readable = True
                if invalid:
                    blockers.append(f"{invalid} V2 object version(s) fail integrity verification")
            except (OSError, ValueError, TypeError):
                blockers.append("V2 recovery inventory is not readable")

    history = (path / ".simpleoffice-history").exists() if exists else False
    control = (path / ".simpleoffice-meta").exists() if exists else False
    return MigrationPreflight(
        root=str(path),
        document_root_exists=exists,
        v2_inventory_readable=inventory_readable,
        v2_invalid_objects=invalid,
        legacy_history_present=history,
        legacy_control_present=control,
        ready=not blockers,
        blockers=tuple(blockers),
    )
