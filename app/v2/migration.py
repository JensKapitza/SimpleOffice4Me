"""V1 -> V2 migration safety helpers.

Preflight is strictly read-only. Source backup is explicit and writes only to a
new destination outside the source tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .blob_store import BlobIntegrityError, BlobStore
from .catalog import FORMAT_FAMILY as CATALOG_FORMAT_FAMILY, SCHEMA_VERSION as CATALOG_SCHEMA_VERSION, ObjectCatalog
from .contracts import LogicalObjectId, StorageLocation


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
                        store.verify(LogicalObjectId(str(manifest["object_id"])), version_id=manifest_path.stem)
                    except (BlobIntegrityError, OSError, ValueError, TypeError, KeyError):
                        invalid += 1
                inventory_readable = True
                if invalid:
                    blockers.append(f"{invalid} V2 object version(s) fail integrity verification")
            except (BlobIntegrityError, OSError, ValueError, TypeError):
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



def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(root: Path, *, exclude: set[str] | None = None) -> str:
    digest = hashlib.sha256()
    excluded = {str(value).replace("\\", "/") for value in (exclude or set())}
    entries = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for entry in entries:
        relative_text = entry.relative_to(root).as_posix()
        if relative_text in excluded:
            continue
        relative = relative_text.encode("utf-8")
        if entry.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(entry).encode("utf-8") + b"\n")
        elif entry.is_dir():
            digest.update(b"D\0" + relative + b"\n")
        elif entry.is_file():
            digest.update(
                b"F\0" + relative + b"\0" + str(entry.stat().st_size).encode("ascii")
                + b"\0" + _sha256_file(entry).encode("ascii") + b"\n"
            )
    return digest.hexdigest()


def build_migration_plan(root: str | Path) -> dict[str, Any]:
    """Build a deterministic read-only plan for legacy document migration."""
    source = Path(root).expanduser().resolve()
    preflight = inspect_migration(source)
    if not preflight.ready:
        return {
            "format": "simpleoffice-v2-migration-plan",
            "format_version": 1,
            "root": str(source),
            "ready": False,
            "documents": 0,
            "ready_documents": 0,
            "blocked_documents": 0,
            "bytes": 0,
            "blockers": list(preflight.blockers),
            "entries": [],
        }

    metadata_dir = source / ".simpleoffice-meta" / "documents"
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    if metadata_dir.is_dir():
        for metadata_path in sorted(metadata_dir.glob("*.json")):
            entry: dict[str, Any] = {
                "metadata": metadata_path.name,
                "document_id": "",
                "path": "",
                "size": 0,
                "sha256": "",
                "status": "blocked",
                "error": "",
            }
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    raise ValueError("metadata is not an object")
                document_id = str(metadata.get("document_id") or "").strip()
                last_path = str(metadata.get("last_path") or "").strip()
                if not document_id:
                    raise ValueError("document_id is missing")
                if not last_path or last_path.startswith("[external]"):
                    raise ValueError("document path is unavailable")
                requested = Path(last_path)
                candidate = requested if requested.is_absolute() else source / requested
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(source)
                if not resolved.is_file():
                    raise ValueError("document path is not a regular file")
                actual_sha = _sha256_file(resolved)
                expected_sha = str(metadata.get("sha256") or "").strip().casefold()
                if expected_sha and expected_sha != actual_sha:
                    raise ValueError("document sha256 does not match metadata")
                size = resolved.stat().st_size
                entry.update({
                    "document_id": document_id,
                    "path": resolved.relative_to(source).as_posix(),
                    "size": size,
                    "sha256": actual_sha,
                    "status": "ready",
                })
                total_bytes += size
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                entry["error"] = str(exc)
            entries.append(entry)

    blocked = [entry for entry in entries if entry["status"] != "ready"]
    return {
        "format": "simpleoffice-v2-migration-plan",
        "format_version": 1,
        "root": str(source),
        "ready": not blocked,
        "documents": len(entries),
        "ready_documents": len(entries) - len(blocked),
        "blocked_documents": len(blocked),
        "bytes": total_bytes,
        "blockers": [f"{entry['metadata']}: {entry['error']}" for entry in blocked],
        "entries": entries,
    }


def _validate_backup_for_plan(source: Path, backup: str | Path, plan: dict[str, Any]) -> Path:
    target = Path(backup).expanduser().resolve(strict=True)
    if target == source or target.is_relative_to(source):
        raise ValueError("migration backup must be outside the source tree")
    manifest_path = target / ".simpleoffice-v2" / "migration-backup.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("migration backup manifest is missing or invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "simpleoffice-v2-migration-backup"
        or int(manifest.get("format_version", 0)) != 1
        or str(manifest.get("source_name") or "") != source.name
    ):
        raise ValueError("migration backup does not match the source installation")

    for entry in plan["entries"]:
        if entry.get("status") != "ready":
            continue
        relative = str(entry["path"])
        try:
            candidate = (target / relative).resolve(strict=True)
            candidate.relative_to(target)
        except (OSError, ValueError) as exc:
            raise ValueError(f"migration backup is missing {relative}") from exc
        if not candidate.is_file():
            raise ValueError(f"migration backup entry is not a file: {relative}")
        if candidate.stat().st_size != int(entry["size"]) or _sha256_file(candidate) != entry["sha256"]:
            raise ValueError(f"migration backup integrity mismatch: {relative}")
    return target


def _catalog_snapshot_read_only(source: Path) -> dict[str, dict[str, Any]]:
    """Read the V2 catalog without creating or modifying it."""
    path = source / ".simpleoffice-v2" / "catalog.sqlite3"
    if not path.is_file():
        return {}
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            meta = dict(db.execute("SELECT key,value FROM catalog_meta").fetchall())
            if (
                str(meta.get("format_family") or "") != CATALOG_FORMAT_FAMILY
                or str(meta.get("schema_version") or "") != str(CATALOG_SCHEMA_VERSION)
            ):
                raise ValueError("unsupported V2 object catalog format")
            rows = db.execute(
                """SELECT object_id,location,version_id,size,content_sha256,state
                   FROM object_catalog"""
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise ValueError("V2 object catalog is unreadable") from exc
    return {str(row["object_id"]): dict(row) for row in rows}


def _catalog_conflicts_for_plan(
    source: Path,
    plan: dict[str, Any],
    store: BlobStore,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    snapshot = _catalog_snapshot_read_only(source)
    by_location = {
        str(row["location"]): str(row["object_id"])
        for row in snapshot.values()
        if str(row.get("state") or "") != "deleted"
    }
    conflicts: list[str] = []
    for entry in plan["entries"]:
        if entry.get("status") != "ready":
            continue
        object_id = LogicalObjectId(str(entry["document_id"]))
        expected_location = StorageLocation(str(entry["path"]))
        row = snapshot.get(object_id.value)
        if row is not None:
            if (
                str(row.get("state") or "") != "active"
                or str(row.get("location") or "") != expected_location.relative_path
                or int(row.get("size") or -1) != int(entry["size"])
                or str(row.get("content_sha256") or "") != str(entry["sha256"])
            ):
                conflicts.append(f"{object_id.value}: catalog state differs from V1")
                continue
            try:
                verified = store.verify(object_id, version_id=str(row.get("version_id") or ""))
            except (BlobIntegrityError, OSError, ValueError, TypeError):
                conflicts.append(f"{object_id.value}: catalog blob version is unavailable")
                continue
            if verified.size != int(entry["size"]) or verified.content_sha256 != str(entry["sha256"]):
                conflicts.append(f"{object_id.value}: catalog blob differs from V1")
                continue
        owner = by_location.get(expected_location.relative_path)
        if owner and owner != object_id.value:
            conflicts.append(
                f"{object_id.value}: location {expected_location.relative_path} belongs to {owner}"
            )
    return snapshot, conflicts


def _write_migration_report(source: Path, report: dict[str, Any]) -> None:
    directory = source / ".simpleoffice-v2"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "migration-transfer.json"
    temporary = directory / f".migration-transfer-{uuid.uuid4().hex}.tmp"
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def transfer_legacy_documents(root: str | Path, backup: str | Path) -> dict[str, Any]:
    """Copy verified V1 content and register its logical V2 catalog state.

    V1 files and metadata remain untouched. Matching BlobStore and ObjectCatalog
    records are reused, making the operation restart-safe and idempotent.
    """
    source = Path(root).expanduser().resolve()
    plan = build_migration_plan(source)
    if not plan["ready"]:
        raise ValueError("migration plan is blocked: " + "; ".join(plan["blockers"]))

    identifiers = [
        str(entry["document_id"])
        for entry in plan["entries"]
        if entry.get("status") == "ready"
    ]
    duplicates = sorted(value for value, count in Counter(identifiers).items() if count > 1)
    if duplicates:
        raise ValueError("migration plan contains duplicate document ids: " + ", ".join(duplicates))

    backup_path = _validate_backup_for_plan(source, backup, plan)
    store = BlobStore(source)
    catalog_snapshot, catalog_conflicts = _catalog_conflicts_for_plan(source, plan, store)
    blob_conflicts: list[str] = []
    for entry in plan["entries"]:
        if entry.get("status") != "ready":
            continue
        object_id = LogicalObjectId(str(entry["document_id"]))
        if object_id.value in catalog_snapshot or not store.contains(object_id):
            continue
        current = store.verify(object_id)
        if current.size != int(entry["size"]) or current.content_sha256 != str(entry["sha256"]):
            blob_conflicts.append(object_id.value)
    conflicts = [*blob_conflicts, *catalog_conflicts]
    if conflicts:
        raise ValueError("existing V2 state conflicts with V1 documents: " + "; ".join(conflicts))

    catalog = ObjectCatalog(source)
    migrated = 0
    already_present = 0
    cataloged = 0
    already_cataloged = 0
    migrated_bytes = 0
    for entry in plan["entries"]:
        if entry.get("status") != "ready":
            continue
        object_id = LogicalObjectId(str(entry["document_id"]))
        existing_catalog = catalog_snapshot.get(object_id.value)
        if existing_catalog is not None:
            version = store.verify(
                object_id,
                version_id=str(existing_catalog["version_id"]),
            )
            already_present += 1
            already_cataloged += 1
            continue

        if store.contains(object_id):
            version = store.verify(object_id)
            if version.size != int(entry["size"]) or version.content_sha256 != str(entry["sha256"]):
                raise ValueError(f"V2 content changed during migration: {object_id.value}")
            already_present += 1
        else:
            source_path = (source / str(entry["path"])).resolve(strict=True)
            source_path.relative_to(source)
            with source_path.open("rb") as handle:
                version = store.write_stream(
                    object_id,
                    handle,
                    expected_size=int(entry["size"]),
                    expected_sha256=str(entry["sha256"]),
                )
            verified = store.verify(object_id, version_id=version.version_id)
            if verified.size != int(entry["size"]) or verified.content_sha256 != str(entry["sha256"]):
                raise RuntimeError(f"V2 verification failed after migration: {object_id.value}")
            migrated += 1
            migrated_bytes += int(entry["size"])

        registered = catalog.register(
            object_id,
            StorageLocation(str(entry["path"])),
            version_id=version.version_id,
            size=version.size,
            content_sha256=version.content_sha256,
        )
        if not registered.ok:
            raise ValueError(
                f"V2 catalog changed during migration: {object_id.value}: "
                f"{registered.error.message if registered.error else 'unknown conflict'}"
            )
        cataloged += 1

    report = {
        "format": "simpleoffice-v2-migration-transfer",
        "format_version": 1,
        "status": "content-copied",
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "documents": int(plan["documents"]),
        "migrated_documents": migrated,
        "already_present_documents": already_present,
        "cataloged_documents": cataloged,
        "already_cataloged_documents": already_cataloged,
        "source_bytes": int(plan["bytes"]),
        "migrated_bytes": migrated_bytes,
        "backup_name": backup_path.name,
        "catalog_format_version": CATALOG_SCHEMA_VERSION,
    }
    _write_migration_report(source, report)
    return report

def verify_migration_transfer(root: str | Path) -> dict[str, Any]:
    """Read-only V1 -> BlobStore -> ObjectCatalog consistency verification."""
    source = Path(root).expanduser().resolve()
    blockers: list[str] = []
    transfer_path = source / ".simpleoffice-v2" / "migration-transfer.json"
    try:
        transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        transfer = {}
        blockers.append("migration transfer report is missing or invalid")

    if not isinstance(transfer, dict):
        blockers.append("migration transfer report is not a JSON object")
        transfer = {}
    elif transfer and (
        transfer.get("format") != "simpleoffice-v2-migration-transfer"
        or int(transfer.get("format_version", 0)) != 1
        or transfer.get("status") != "content-copied"
    ):
        blockers.append("migration transfer report has an unsupported state")

    plan = build_migration_plan(source)
    if not plan["ready"]:
        blockers.extend(str(item) for item in plan["blockers"])
        return {
            "format": "simpleoffice-v2-migration-verification",
            "format_version": 1,
            "ready": False,
            "documents": int(plan["documents"]),
            "verified_documents": 0,
            "source_bytes": int(plan["bytes"]),
            "blockers": blockers,
        }

    if transfer:
        if int(transfer.get("documents", -1)) != int(plan["documents"]):
            blockers.append("migration transfer document count no longer matches the V1 plan")
        if int(transfer.get("source_bytes", -1)) != int(plan["bytes"]):
            blockers.append("migration transfer byte count no longer matches the V1 plan")
        transferred = int(transfer.get("migrated_documents", 0)) + int(
            transfer.get("already_present_documents", 0)
        )
        if transferred != int(plan["documents"]):
            blockers.append("migration transfer report does not cover every V1 document")
        cataloged = int(transfer.get("cataloged_documents", 0)) + int(
            transfer.get("already_cataloged_documents", 0)
        )
        if cataloged != int(plan["documents"]):
            blockers.append("migration transfer report does not cover every V2 catalog entry")

    blob_base = source / ".simpleoffice-v2" / "blob-store"
    catalog_path = source / ".simpleoffice-v2" / "catalog.sqlite3"
    if not blob_base.is_dir():
        blockers.append("V2 blob store is missing after migration transfer")
    if not catalog_path.is_file():
        blockers.append("V2 object catalog is missing after migration transfer")
    if not transfer or blockers:
        return {
            "format": "simpleoffice-v2-migration-verification",
            "format_version": 1,
            "ready": False,
            "documents": int(plan["documents"]),
            "verified_documents": 0,
            "source_bytes": int(plan["bytes"]),
            "blockers": blockers,
        }

    try:
        catalog = _catalog_snapshot_read_only(source)
    except ValueError as exc:
        blockers.append(str(exc))
        catalog = {}
    if blockers:
        return {
            "format": "simpleoffice-v2-migration-verification",
            "format_version": 1,
            "ready": False,
            "documents": int(plan["documents"]),
            "verified_documents": 0,
            "source_bytes": int(plan["bytes"]),
            "blockers": blockers,
        }

    store = BlobStore(source)
    verified = 0
    for entry in plan["entries"]:
        if entry.get("status") != "ready":
            continue
        object_id = LogicalObjectId(str(entry["document_id"]))
        row = catalog.get(object_id.value)
        if row is None:
            blockers.append(f"V2 catalog object is missing: {object_id.value}")
            continue
        if (
            str(row.get("state") or "") != "active"
            or str(row.get("location") or "") != str(entry["path"])
            or int(row.get("size") or -1) != int(entry["size"])
            or str(row.get("content_sha256") or "") != str(entry["sha256"])
        ):
            blockers.append(f"V2 catalog object differs from V1 source: {object_id.value}")
            continue
        if not store.contains(object_id):
            blockers.append(f"V2 object is missing: {object_id.value}")
            continue
        try:
            current = store.verify(
                object_id,
                version_id=str(row.get("version_id") or ""),
            )
        except (BlobIntegrityError, OSError, ValueError) as exc:
            blockers.append(f"V2 object failed integrity verification: {object_id.value}: {exc}")
            continue
        if (
            current.version_id != str(row.get("version_id") or "")
            or current.size != int(entry["size"])
            or current.content_sha256 != str(entry["sha256"])
        ):
            blockers.append(f"V2 object differs from V1/catalog state: {object_id.value}")
            continue
        verified += 1

    return {
        "format": "simpleoffice-v2-migration-verification",
        "format_version": 1,
        "ready": not blockers,
        "documents": int(plan["documents"]),
        "verified_documents": verified,
        "source_bytes": int(plan["bytes"]),
        "blockers": blockers,
    }

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
    reserved_manifest = source / ".simpleoffice-v2" / "migration-backup.json"
    if reserved_manifest.exists() or reserved_manifest.is_symlink():
        raise ValueError("source contains the reserved migration backup manifest path")
    target.parent.mkdir(parents=True, exist_ok=True)

    inventory = _source_inventory(source)
    source_v2_dir_present = (source / ".simpleoffice-v2").is_dir()
    source_tree_sha256 = _tree_sha256(source)
    staging = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    if staging.exists():
        raise FileExistsError("migration backup staging path already exists")
    try:
        shutil.copytree(source, staging, symlinks=True)
        backup_tree_sha256 = _tree_sha256(staging)
        if backup_tree_sha256 != source_tree_sha256:
            raise RuntimeError("source changed while migration backup was being created")
        metadata_dir = staging / ".simpleoffice-v2"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": "simpleoffice-v2-migration-backup",
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_name": source.name,
            "tree_sha256": backup_tree_sha256,
            "source_v2_dir_present": source_v2_dir_present,
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


def restore_migration_backup(backup: str | Path, destination: str | Path) -> dict[str, Any]:
    """Restore a migration backup atomically into a new, empty destination."""
    source = Path(backup).expanduser().resolve(strict=True)
    target = Path(destination).expanduser().resolve()
    if target.exists():
        raise FileExistsError("migration restore destination already exists")
    if target == source or target.is_relative_to(source):
        raise ValueError("migration restore destination must be outside the backup tree")

    manifest_path = source / ".simpleoffice-v2" / "migration-backup.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("migration backup manifest is missing or invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "simpleoffice-v2-migration-backup"
        or int(manifest.get("format_version", 0)) != 1
    ):
        raise ValueError("unsupported migration backup format")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}")
    try:
        shutil.copytree(source, staging, symlinks=True)
        copied_manifest = staging / ".simpleoffice-v2" / "migration-backup.json"
        copied_manifest.unlink()
        v2_directory = staging / ".simpleoffice-v2"
        expected_inventory = {
            key: int(manifest.get(key, -1))
            for key in ("files", "directories", "symlinks", "bytes")
        }
        inventory = _source_inventory(staging)
        should_remove_added_v2_dir = manifest.get("source_v2_dir_present") is False
        if (
            inventory != expected_inventory
            and should_remove_added_v2_dir
            and v2_directory.is_dir()
            and not any(v2_directory.iterdir())
        ):
            v2_directory.rmdir()
            inventory = _source_inventory(staging)
        elif (
            inventory != expected_inventory
            and "source_v2_dir_present" not in manifest
            and v2_directory.is_dir()
            and not any(v2_directory.iterdir())
        ):
            # Backward compatibility with backups created before the marker existed.
            v2_directory.rmdir()
            inventory = _source_inventory(staging)
        if inventory != expected_inventory:
            raise ValueError("migration backup inventory does not match its manifest")
        expected_tree = str(manifest.get("tree_sha256") or "")
        actual_tree = _tree_sha256(staging)
        if expected_tree and actual_tree != expected_tree:
            raise ValueError("migration backup tree integrity check failed")
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        "format": "simpleoffice-v2-migration-restore",
        "format_version": 1,
        "status": "restored",
        "destination": str(target),
        "files": inventory["files"],
        "directories": inventory["directories"],
        "symlinks": inventory["symlinks"],
        "bytes": inventory["bytes"],
        "tree_sha256": actual_tree,
        "integrity": "sha256-tree" if expected_tree else "legacy-inventory-only",
    }
