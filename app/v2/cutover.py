"""Persistent, explicit V2 storage cutover state.

The state file never enables V2 merely because V2 data exists.  Missing state
means V1.  Shadow mode requires a successful V1 -> V2 verification and keeps
legacy data intact.  The final V2 activation is intentionally a separate step
because all runtime mutation/read consumers must first be migrated.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .blob_store import BlobIntegrityError, BlobStore
from .contracts import LogicalObjectId
from .migration import _catalog_snapshot_read_only, build_migration_plan, verify_migration_transfer


FORMAT_FAMILY = "simpleoffice-v2-storage-cutover"
FORMAT_VERSION = 1
MODES = {"v1", "shadow"}
PROTECTION_MODES = {"unspecified", "local-plaintext"}
LOCAL_PLAINTEXT = "local-plaintext"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CutoverState:
    mode: str = "v1"
    protection_mode: str = "unspecified"
    migration_fingerprint: str = ""
    verified_at: str = ""
    updated_at: str = ""
    dirty: bool = False
    dirty_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_FAMILY,
            "format_version": FORMAT_VERSION,
            **asdict(self),
        }


def _state_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / ".simpleoffice-v2" / "storage-cutover.json"


def migration_fingerprint(root: str | Path) -> str:
    """Fingerprint only migration-relevant public integrity metadata."""
    plan = build_migration_plan(root)
    if not plan.get("ready"):
        raise ValueError("migration plan is not ready")
    entries = [
        {
            "document_id": str(row["document_id"]),
            "path": str(row["path"]),
            "size": int(row["size"]),
            "sha256": str(row["sha256"]),
        }
        for row in plan.get("entries", [])
        if row.get("status") == "ready"
    ]
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _shadow_inventory(root: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    source = Path(root).expanduser().resolve()
    metadata_dir = source / ".simpleoffice-meta" / "documents"
    if not metadata_dir.exists():
        return [], []
    if not metadata_dir.is_dir() or metadata_dir.is_symlink():
        return [], ["legacy document metadata directory is unavailable"]

    entries: list[dict[str, Any]] = []
    blockers: list[str] = []
    seen: set[str] = set()
    for metadata_path in sorted(metadata_dir.glob("*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("metadata is not an object")
            object_id = str(metadata.get("document_id") or "").strip()
            if not object_id:
                raise ValueError("document_id is missing")
            if object_id in seen:
                raise ValueError("duplicate document_id")
            seen.add(object_id)
            expected_sha = str(metadata.get("sha256") or "").strip().casefold()
            if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
                raise ValueError("document sha256 is missing or invalid")

            deleted = bool(
                metadata.get("deleted_at")
                or metadata.get("system_state") == "webdav_deleted"
            )
            if deleted:
                location = str(metadata.get("deleted_from") or "").strip()
                recovery = str(metadata.get("recovery_path") or "").strip()
                if not location or not recovery:
                    raise ValueError("deleted document recovery metadata is incomplete")
                candidate = (source / ".simpleoffice-meta" / recovery).resolve(strict=True)
                candidate.relative_to((source / ".simpleoffice-meta").resolve())
                if not candidate.is_file() or candidate.is_symlink():
                    raise ValueError("deleted document recovery payload is unavailable")
                state = "deleted"
            else:
                location = str(metadata.get("last_path") or "").strip()
                if not location or location.startswith("[external]"):
                    raise ValueError("active document path is unavailable")
                requested = Path(location)
                candidate = requested if requested.is_absolute() else source / requested
                candidate = candidate.resolve(strict=True)
                candidate.relative_to(source)
                if not candidate.is_file() or candidate.is_symlink():
                    raise ValueError("active document path is not a regular file")
                location = candidate.relative_to(source).as_posix()
                state = "active"

            actual_sha = _sha256_file(candidate)
            if actual_sha != expected_sha:
                raise ValueError("document content does not match metadata sha256")
            entries.append({
                "document_id": object_id,
                "state": state,
                "location": location,
                "size": candidate.stat().st_size,
                "sha256": actual_sha,
            })
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            blockers.append(f"{metadata_path.name}: {exc}")
    return entries, blockers


def _shadow_fingerprint(entries: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        sorted(entries, key=lambda row: str(row["document_id"])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_shadow_consistency(root: str | Path) -> dict[str, Any]:
    """Read-only comparison of the current V1 projection with V2 catalog/blob state."""
    source = Path(root).expanduser().resolve()
    entries, blockers = _shadow_inventory(source)
    fingerprint = _shadow_fingerprint(entries)

    try:
        catalog = _catalog_snapshot_read_only(source)
    except ValueError as exc:
        catalog = {}
        blockers.append(str(exc))

    blob_base = source / ".simpleoffice-v2" / "blob-store"
    if entries and not blob_base.is_dir():
        blockers.append("V2 blob store is missing")
        store = None
    else:
        store = BlobStore(source) if blob_base.is_dir() else None

    expected_ids = {str(row["document_id"]) for row in entries}
    extra_ids = sorted(set(catalog) - expected_ids)
    for object_id in extra_ids:
        blockers.append(f"V2 catalog contains object without V1 metadata: {object_id}")

    verified = 0
    active = 0
    deleted = 0
    for entry in entries:
        object_id = str(entry["document_id"])
        if entry["state"] == "deleted":
            deleted += 1
        else:
            active += 1
        row = catalog.get(object_id)
        if row is None:
            blockers.append(f"V2 catalog object is missing: {object_id}")
            continue
        if (
            str(row.get("state") or "") != str(entry["state"])
            or str(row.get("location") or "") != str(entry["location"])
            or int(row.get("size") or -1) != int(entry["size"])
            or str(row.get("content_sha256") or "") != str(entry["sha256"])
        ):
            blockers.append(f"V1/V2 catalog state differs: {object_id}")
            continue
        if store is None:
            continue
        logical = LogicalObjectId(object_id)
        try:
            version = store.verify(logical, version_id=str(row.get("version_id") or ""))
        except (BlobIntegrityError, OSError, ValueError, TypeError) as exc:
            blockers.append(f"V2 blob verification failed: {object_id}: {exc}")
            continue
        if (
            version.size != int(entry["size"])
            or version.content_sha256 != str(entry["sha256"])
            or version.version_id != str(row.get("version_id") or "")
        ):
            blockers.append(f"V1/V2 blob state differs: {object_id}")
            continue
        verified += 1

    return {
        "format": "simpleoffice-v2-shadow-verification",
        "format_version": 1,
        "ready": not blockers,
        "documents": len(entries),
        "active_documents": active,
        "deleted_documents": deleted,
        "verified_documents": verified,
        "fingerprint": fingerprint,
        "blockers": blockers,
    }


def load_cutover_state(root: str | Path) -> CutoverState:
    """Read state without creating files. Missing state is the safe V1 default."""
    path = _state_path(root)
    if not path.exists():
        return CutoverState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("V2 storage cutover state is unreadable") from exc
    if not isinstance(raw, dict):
        raise ValueError("V2 storage cutover state is not an object")
    if raw.get("format") != FORMAT_FAMILY or int(raw.get("format_version", 0)) != FORMAT_VERSION:
        raise ValueError("unsupported V2 storage cutover state format")
    mode = str(raw.get("mode") or "")
    if mode not in MODES:
        raise ValueError("unsupported V2 storage cutover mode")
    protection_mode = str(raw.get("protection_mode") or "unspecified")
    if protection_mode not in PROTECTION_MODES:
        raise ValueError("unsupported V2 storage protection mode")
    fingerprint = str(raw.get("migration_fingerprint") or "")
    if fingerprint and (len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint)):
        raise ValueError("invalid V2 migration fingerprint")
    return CutoverState(
        mode=mode,
        protection_mode=protection_mode,
        migration_fingerprint=fingerprint,
        verified_at=str(raw.get("verified_at") or ""),
        updated_at=str(raw.get("updated_at") or ""),
        dirty=bool(raw.get("dirty", False)),
        dirty_reason=str(raw.get("dirty_reason") or "")[:500],
    )


def _write_cutover_state(root: str | Path, state: CutoverState) -> CutoverState:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if os.name == "posix":
            try:
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
    finally:
        temporary.unlink(missing_ok=True)
    return state


def cutover_status(root: str | Path) -> dict[str, Any]:
    """Return persisted state plus a fresh read-only V1/V2 verification."""
    state = load_cutover_state(root)
    if state.mode == "shadow":
        verification = verify_shadow_consistency(root)
        current_fingerprint = str(verification.get("fingerprint") or "")
        ready_for_shadow = bool(verification.get("ready"))
    else:
        verification = verify_migration_transfer(root)
        current_fingerprint = migration_fingerprint(root) if verification.get("ready") else ""
        ready_for_shadow = bool(verification.get("ready"))
    fingerprint_matches = bool(
        state.migration_fingerprint
        and current_fingerprint
        and state.migration_fingerprint == current_fingerprint
    )
    return {
        **state.to_dict(),
        "verification_ready": bool(verification.get("ready")),
        "verification_blockers": list(verification.get("blockers") or []),
        "current_migration_fingerprint": current_fingerprint,
        "fingerprint_matches": fingerprint_matches,
        "ready_for_shadow": ready_for_shadow,
        "shadow_dirty": bool(state.dirty),
        "protection_mode": state.protection_mode,
        "encrypted_at_rest": False,
        "federation_storage_allowed": False,
        "storage_protection_warning": (
            "V2 local storage is explicitly unencrypted at rest; do not use this blob store "
            "as ciphertext storage for federation or P2P peers."
            if state.protection_mode == LOCAL_PLAINTEXT
            else "V2 storage protection mode has not been explicitly selected."
        ),
        "ready_for_v2_activation": False,
        "v2_activation_blocker": (
            "runtime cutover is not enabled until all required read/write consumers "
            "use the V2 storage boundary"
        ),
    }


def prepare_shadow(
    root: str | Path,
    *,
    apply: bool = False,
    acknowledge_local_plaintext: bool = False,
) -> dict[str, Any]:
    """Verify V1/V2 equivalence and optionally persist or refresh shadow mode."""
    state = load_cutover_state(root)
    if state.mode == "shadow":
        verification = verify_shadow_consistency(root)
        label = "shadow"
    else:
        migration = verify_migration_transfer(root)
        if not migration.get("ready"):
            raise ValueError(
                "V2 shadow mode requires a clean migration verification: "
                + "; ".join(str(item) for item in migration.get("blockers") or [])
            )
        verification = verify_shadow_consistency(root)
        label = "migration"
    if not verification.get("ready"):
        raise ValueError(
            f"V2 shadow mode requires clean {label} consistency: "
            + "; ".join(str(item) for item in verification.get("blockers") or [])
        )
    fingerprint = str(verification.get("fingerprint") or "")
    if apply and not acknowledge_local_plaintext:
        raise ValueError(
            "V2 shadow mode currently uses local plaintext storage; explicit acknowledgement required"
        )
    preview = {
        "mode": "shadow",
        "migration_fingerprint": fingerprint,
        "verified_documents": int(verification.get("verified_documents", 0)),
        "protection_mode": LOCAL_PLAINTEXT,
        "encrypted_at_rest": False,
        "federation_storage_allowed": False,
        "plaintext_acknowledged": bool(acknowledge_local_plaintext),
        "source_bytes": sum(
            int(row.get("size") or 0)
            for row in _shadow_inventory(root)[0]
        ),
        "applied": bool(apply),
    }
    if not apply:
        return preview
    now = _now()
    refreshed = CutoverState(
        mode="shadow",
        protection_mode=LOCAL_PLAINTEXT,
        migration_fingerprint=fingerprint,
        verified_at=now,
        updated_at=now,
        dirty=False,
        dirty_reason="",
    )
    _write_cutover_state(root, refreshed)
    return preview


def mark_shadow_dirty(root: str | Path, reason: str) -> CutoverState:
    """Persist that shadow equivalence must be re-established before cutover."""
    state = load_cutover_state(root)
    if state.mode != "shadow":
        return state
    updated = replace(
        state,
        dirty=True,
        dirty_reason=str(reason or "shadow state changed")[:500],
        updated_at=_now(),
    )
    return _write_cutover_state(root, updated)


def return_to_v1(root: str | Path, *, apply: bool = False) -> dict[str, Any]:
    """Explicitly leave shadow mode without deleting any V2 data."""
    state = load_cutover_state(root)
    result = {
        "previous_mode": state.mode,
        "mode": "v1",
        "v2_data_retained": True,
        "applied": bool(apply),
    }
    if apply:
        _write_cutover_state(
            root,
            CutoverState(mode="v1", protection_mode="unspecified", updated_at=_now()),
        )
    return result
