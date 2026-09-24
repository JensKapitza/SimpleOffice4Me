"""Phase-14 migration smoke test and completion orchestration.

The existing migration helpers own preflight, backup, transfer, verification and
restore. This module adds the final read-only smoke step and persists completion
through the existing storage-cutover state by entering verified shadow mode.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .blob_store import BlobIntegrityError, BlobStore
from .contracts import LogicalObjectId
from .cutover import (
    LOCAL_PLAINTEXT,
    load_cutover_state,
    prepare_shadow,
    verify_shadow_consistency,
)
from .migration import build_migration_plan, verify_migration_transfer


FORMAT = "simpleoffice-v2-migration-acceptance"
FORMAT_VERSION = 1


def _result(
    *,
    ready: bool,
    applied: bool,
    completed: bool,
    mode: str,
    documents: int,
    verified_documents: int,
    smoke_object_id: str,
    blockers: list[str],
    fingerprint: str = "",
) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "ready": bool(ready),
        "applied": bool(applied),
        "completed": bool(completed),
        "mode": str(mode),
        "documents": int(documents),
        "verified_documents": int(verified_documents),
        "smoke_object_id": str(smoke_object_id),
        "blockers": list(blockers),
        "migration_fingerprint": str(fingerprint),
        "completion_marker": ".simpleoffice-v2/storage-cutover.json",
        "completion_marker_present": mode in {"shadow", "v2"},
    }


def migration_smoke_test(root: str | Path) -> dict[str, Any]:
    """Read-only smoke test after transfer verification.

    Full integrity verification remains authoritative. The smoke step performs
    one deterministic physical read through the migrated V2 blob store so the
    migration does not finish on metadata checks alone.
    """

    source = Path(root).expanduser().resolve()
    verification = verify_migration_transfer(source)
    blockers = list(verification.get("blockers") or [])
    documents = int(verification.get("documents") or 0)
    verified_documents = int(verification.get("verified_documents") or 0)
    if not verification.get("ready"):
        return _result(
            ready=False,
            applied=False,
            completed=False,
            mode=load_cutover_state(source).mode,
            documents=documents,
            verified_documents=verified_documents,
            smoke_object_id="",
            blockers=blockers,
        )

    shadow = verify_shadow_consistency(source)
    if not shadow.get("ready"):
        blockers.extend(str(value) for value in shadow.get("blockers") or [])
        return _result(
            ready=False,
            applied=False,
            completed=False,
            mode=load_cutover_state(source).mode,
            documents=documents,
            verified_documents=verified_documents,
            smoke_object_id="",
            blockers=blockers,
            fingerprint=str(shadow.get("fingerprint") or ""),
        )

    plan = build_migration_plan(source)
    ready_entries = sorted(
        (
            row
            for row in plan.get("entries", [])
            if row.get("status") == "ready"
        ),
        key=lambda row: str(row.get("document_id") or ""),
    )
    smoke_object_id = ""
    if ready_entries:
        sample = ready_entries[0]
        smoke_object_id = str(sample["document_id"])
        try:
            store = BlobStore(source)
            payload = store.read(LogicalObjectId(smoke_object_id))
            if len(payload) != int(sample["size"]):
                raise ValueError("smoke-read size differs from migration plan")
            digest = hashlib.sha256(payload).hexdigest()
            if digest != str(sample["sha256"]):
                raise ValueError("smoke-read digest differs from migration plan")
        except (BlobIntegrityError, OSError, TypeError, ValueError) as exc:
            blockers.append(f"V2 smoke read failed: {smoke_object_id}: {exc}")

    return _result(
        ready=not blockers,
        applied=False,
        completed=False,
        mode=load_cutover_state(source).mode,
        documents=documents,
        verified_documents=verified_documents,
        smoke_object_id=smoke_object_id,
        blockers=blockers,
        fingerprint=str(shadow.get("fingerprint") or ""),
    )


def finalize_migration(
    root: str | Path,
    *,
    apply: bool = False,
    acknowledge_local_plaintext: bool = False,
) -> dict[str, Any]:
    """Finish Phase 14 by persisting the verified shadow completion marker."""

    source = Path(root).expanduser().resolve()
    state = load_cutover_state(source)

    if state.mode == "v2":
        return _result(
            ready=True,
            applied=bool(apply),
            completed=True,
            mode="v2",
            documents=0,
            verified_documents=0,
            smoke_object_id="",
            blockers=[],
            fingerprint=state.migration_fingerprint,
        )

    if state.mode == "shadow":
        smoke = migration_smoke_test(source)
        blockers = list(smoke.get("blockers") or [])
        fingerprint = str(smoke.get("migration_fingerprint") or "")
        if state.dirty:
            blockers.append(
                "shadow completion marker is dirty; refresh the migration acceptance before V2 activation"
            )
        if not state.migration_fingerprint or state.migration_fingerprint != fingerprint:
            blockers.append("shadow completion marker fingerprint no longer matches current migration state")
        return _result(
            ready=bool(smoke.get("ready")) and not blockers,
            applied=bool(apply),
            completed=bool(smoke.get("ready")) and not blockers,
            mode="shadow",
            documents=int(smoke.get("documents") or 0),
            verified_documents=int(smoke.get("verified_documents") or 0),
            smoke_object_id=str(smoke.get("smoke_object_id") or ""),
            blockers=blockers,
            fingerprint=fingerprint,
        )

    smoke = migration_smoke_test(source)
    if not smoke["ready"]:
        return smoke

    if not apply:
        return {
            **smoke,
            "ready": True,
            "applied": False,
            "completed": False,
            "mode": "v1",
            "completion_marker_present": False,
        }

    if not acknowledge_local_plaintext:
        return {
            **smoke,
            "ready": False,
            "applied": False,
            "completed": False,
            "mode": "v1",
            "blockers": [
                "explicit acknowledgement of the current local plaintext V2 store is required"
            ],
            "completion_marker_present": False,
        }

    prepared = prepare_shadow(
        source,
        apply=True,
        acknowledge_local_plaintext=True,
    )
    persisted = load_cutover_state(source)
    if persisted.mode != "shadow" or persisted.protection_mode != LOCAL_PLAINTEXT:
        raise RuntimeError("migration completion marker was not persisted as verified shadow mode")
    if persisted.migration_fingerprint != str(prepared.get("migration_fingerprint") or ""):
        raise RuntimeError("migration completion marker fingerprint differs from verified shadow state")

    return _result(
        ready=True,
        applied=True,
        completed=True,
        mode="shadow",
        documents=int(smoke["documents"]),
        verified_documents=int(smoke["verified_documents"]),
        smoke_object_id=str(smoke["smoke_object_id"]),
        blockers=[],
        fingerprint=persisted.migration_fingerprint,
    )
