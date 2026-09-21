"""V1 -> V2 migration safety helpers.

Preflight is strictly read-only. Source backup is explicit and writes only to a
new destination outside the source tree.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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


def _source_inventory(root: Path) -> dict[str, int]:
    files = 0
    directories = 0
    symlinks = 0
    bytes_total = 0
    for entry in root.rglob("*"):
        if entry.is_symlink():
            symlinks += 1
        elif entry.is_dir():
            directories += 1
        elif entry.is_file():
            files += 1
            bytes_total += entry.stat().st_size
    return {
        "files": files,
        "directories": directories,
        "symlinks": symlinks,
        "bytes": bytes_total,
    }


def create_migration_backup(root: str | Path, destination: str | Path) -> dict[str, Any]:
    """Create an atomic source-tree backup before any future migration.

    Symlinks are copied as links rather than dereferenced, so a link cannot make
    backup creation read arbitrary data outside the document root.
    """
    source = Path(root).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    preflight = inspect_migration(source)
    if not preflight.ready:
        raise ValueError("migration preflight failed: " + "; ".join(preflight.blockers))
    if target == source or target.is_relative_to(source):
        raise ValueError("migration backup destination must be outside the source tree")
    if target.exists():
        raise FileExistsError("migration backup destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)

    inventory = _source_inventory(source)
    staging = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    if staging.exists():
        raise FileExistsError("migration backup staging path already exists")
    try:
        shutil.copytree(source, staging, symlinks=True)
        metadata_dir = staging / ".simpleoffice-v2"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": "simpleoffice-v2-migration-backup",
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_name": source.name,
            **inventory,
        }
        (metadata_dir / "migration-backup.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**manifest, "destination": str(target)}
