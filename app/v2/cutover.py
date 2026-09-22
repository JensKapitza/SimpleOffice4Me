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

from .migration import build_migration_plan, verify_migration_transfer


FORMAT_FAMILY = "simpleoffice-v2-storage-cutover"
FORMAT_VERSION = 1
MODES = {"v1", "shadow"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CutoverState:
    mode: str = "v1"
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
    fingerprint = str(raw.get("migration_fingerprint") or "")
    if fingerprint and (len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint)):
        raise ValueError("invalid V2 migration fingerprint")
    return CutoverState(
        mode=mode,
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
    """Return persisted state plus a fresh read-only migration verification."""
    state = load_cutover_state(root)
    verification = verify_migration_transfer(root)
    current_fingerprint = ""
    if verification.get("ready"):
        current_fingerprint = migration_fingerprint(root)
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
        "ready_for_shadow": bool(verification.get("ready")),
        "ready_for_v2_activation": False,
        "v2_activation_blocker": (
            "runtime cutover is not enabled until all required read/write consumers "
            "use the V2 storage boundary"
        ),
    }


def prepare_shadow(root: str | Path, *, apply: bool = False) -> dict[str, Any]:
    """Verify V1/V2 equivalence and optionally persist explicit shadow mode."""
    verification = verify_migration_transfer(root)
    if not verification.get("ready"):
        raise ValueError(
            "V2 shadow mode requires a clean migration verification: "
            + "; ".join(str(item) for item in verification.get("blockers") or [])
        )
    fingerprint = migration_fingerprint(root)
    preview = {
        "mode": "shadow",
        "migration_fingerprint": fingerprint,
        "verified_documents": int(verification.get("verified_documents", 0)),
        "source_bytes": int(verification.get("source_bytes", 0)),
        "applied": bool(apply),
    }
    if not apply:
        return preview
    now = _now()
    state = CutoverState(
        mode="shadow",
        migration_fingerprint=fingerprint,
        verified_at=now,
        updated_at=now,
        dirty=False,
        dirty_reason="",
    )
    _write_cutover_state(root, state)
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
        _write_cutover_state(root, CutoverState(mode="v1", updated_at=_now()))
    return result
