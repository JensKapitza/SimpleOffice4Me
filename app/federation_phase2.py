"""SOFP phase 2: multi-source pulls, repair/rebalancing and typed resource sync.

The implementation deliberately discovers sources only among configured peers and
keeps the existing default-deny peer policy.  A single blob may be reconstructed
from chunks served by several peers.  Every chunk and the complete blob are
verified before a transfer can become complete.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import click
from flask import Blueprint, Response, current_app, jsonify, request

from .calendar_store import CalendarStore
from .federation_core import (
    complete,
    manifest_valid,
    normalize_sha256,
    preallocate,
    rarest_first,
    select_source,
    source_score,
    transfer_id,
    verify_chunk,
    verify_file,
    write_chunk,
    choose_replication_targets,
)
from .federation_store import FederationStore
from .federation_worker import (
    _find_blob,
    _request,
    push_blob_to_peer,
    remote_availability,
    remote_blob_manifest,
)
from .todo_store import TodoStore


bp = Blueprint("federation_phase2", __name__, url_prefix="/federation/v1/resources")
MAX_RESOURCE_BYTES = 16 * 1024 * 1024


def _authorized() -> bool:
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not expected:
        return bool(current_app.testing)
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(expected, supplied)


@bp.before_request
def authenticate_resources():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _policy_allows_receive(peer: dict[str, Any], resource: str) -> bool:
    resources = (peer.get("policy") or {}).get("resources") or {}
    return bool((resources.get(resource) or {}).get("receive", False))


def _availability_set(payload: dict[str, Any], total: int) -> set[int]:
    result: set[int] = set()
    for row in payload.get("available") or []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        try:
            start, end = int(row[0]), int(row[1])
        except (TypeError, ValueError):
            continue
        if start < 0 or end < start:
            continue
        result.update(range(start, min(end, total - 1) + 1))
    return result


def _manifest_signature(manifest: dict[str, Any]) -> tuple[Any, ...]:
    return (
        normalize_sha256(manifest.get("blob_hash", "")),
        int(manifest.get("size", 0)),
        int(manifest.get("chunk_size", 0)),
        tuple((int(row.get("index", -1)), int(row.get("length", -1)), normalize_sha256(row.get("hash", ""))) for row in manifest.get("chunks") or []),
    )


def discover_blob_sources(root: str | Path, digest: str, peer_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """Find compatible configured peers that can provide chunks of ``digest``."""
    digest = normalize_sha256(digest)
    store = FederationStore(root)
    selected = set(peer_ids or [])
    peers = [
        peer for peer in store.list_peers()
        if peer.get("enabled")
        and (not selected or peer["peer_id"] in selected)
        and _policy_allows_receive(peer, "documents")
    ]
    canonical: dict[str, Any] | None = None
    canonical_signature: tuple[Any, ...] | None = None
    sources: dict[str, set[int]] = {}
    scores: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for peer in peers:
        peer_id = peer["peer_id"]
        started = time.perf_counter()
        try:
            manifest = remote_blob_manifest(root, peer_id, digest)
            if not manifest_valid(manifest) or normalize_sha256(manifest.get("blob_hash", "")) != digest:
                raise ValueError("invalid remote manifest")
            signature = _manifest_signature(manifest)
            if canonical is None:
                canonical, canonical_signature = manifest, signature
            elif signature != canonical_signature:
                raise ValueError("peer manifest differs from canonical blob manifest")
            availability = remote_availability(root, peer_id, digest)
            available = _availability_set(availability, int(manifest.get("chunk_count", 0)))
            if not available and int(manifest.get("chunk_count", 0)):
                raise ValueError("peer advertises no usable chunks")
            elapsed_ms = max(0.1, (time.perf_counter() - started) * 1000.0)
            failures = 1 if peer.get("last_error") else 0
            scores[peer_id] = source_score(max(1.0, len(available)), elapsed_ms, failures)
            sources[peer_id] = available
            details[peer_id] = {"base_url": peer["base_url"], "chunks": len(available), "rtt_ms": round(elapsed_ms, 2)}
            store.set_peer_health(peer_id, seen=True)
        except Exception as exc:
            errors[peer_id] = str(exc)[:500]
            store.set_peer_health(peer_id, error=str(exc))
    if canonical is None or not sources:
        raise ValueError("no configured federation peer can provide this blob")
    total = int(canonical.get("chunk_count", 0))
    if set().union(*sources.values()) != set(range(total)):
        missing = sorted(set(range(total)) - set().union(*sources.values()))
        raise ValueError(f"configured peers cannot provide all chunks; missing={missing[:20]}")
    return {"manifest": canonical, "sources": sources, "scores": scores, "details": details, "errors": errors}


def pull_blob_multisource(
    root: str | Path,
    digest: str,
    *,
    peer_ids: list[str] | tuple[str, ...] | None = None,
    job_id: str = "",
    operation: str = "COPY",
) -> dict[str, Any]:
    """Reconstruct a blob from one or more configured peers with persistent resume."""
    digest = normalize_sha256(digest)
    root = Path(root).expanduser().resolve()
    federation = FederationStore(root)
    discovered = discover_blob_sources(root, digest, peer_ids)
    manifest = discovered["manifest"]
    chunks = manifest.get("chunks") or []
    total = int(manifest.get("chunk_count", len(chunks)))
    job_id = (job_id.strip() or f"multisource-{digest[:24]}")[:160]
    partial = federation.incoming / f"{job_id}.part"
    transfer = federation.get_transfer(job_id)
    if transfer:
        if normalize_sha256(transfer.get("blob_hash", "")) != digest or _manifest_signature(transfer.get("manifest") or {}) != _manifest_signature(manifest):
            raise ValueError("existing transfer is incompatible with requested blob")
        partial = Path(str(transfer.get("final_path") or partial))
        if transfer.get("status") == "complete" and Path(str(transfer.get("final_path") or "")).is_file():
            final = Path(str(transfer["final_path"]))
            if verify_file(final, digest):
                return {"transfer_id": job_id, "status": "complete", "path": str(final), "sources": discovered["details"], "resumed": True}
    else:
        preallocate(partial, int(manifest.get("size", 0)))
        federation.create_transfer(
            job_id,
            direction="incoming-multisource",
            operation=operation.upper(),
            blob_hash=digest,
            status="running",
            total_bytes=int(manifest.get("size", 0)),
            total_chunks=total,
            manifest=manifest,
        )
        federation.update_transfer(job_id, final_path=str(partial))
    if not partial.exists():
        preallocate(partial, int(manifest.get("size", 0)))
        federation.update_transfer(job_id, final_path=str(partial), transferred_bytes=0, have_bitmap="")
    have = federation.have(job_id)
    source_failures = {peer_id: 0 for peer_id in discovered["sources"]}
    ordered = rarest_first(have, discovered["sources"], total)
    for index in ordered:
        chunk = chunks[index]
        candidates = dict(discovered["sources"])
        while True:
            dynamic_scores = {
                peer_id: discovered["scores"].get(peer_id, 0.0) / (1 + source_failures.get(peer_id, 0))
                for peer_id in candidates
            }
            peer_id = select_source(index, candidates, dynamic_scores)
            if not peer_id:
                federation.update_transfer(job_id, status="failed", error=f"no source left for chunk {index}")
                raise ValueError(f"no source left for chunk {index}")
            peer = federation.get_peer(peer_id)
            if not peer:
                candidates.pop(peer_id, None)
                continue
            try:
                token = federation.peer_token(peer_id)
                endpoint = f"{peer['base_url']}/federation/v1/blobs/{digest}/chunks/{index}"
                with _request(endpoint, token=token, timeout=120) as response:
                    data = response.read(int(chunk["length"]) + 1)
                if len(data) != int(chunk["length"]) or not verify_chunk(data, str(chunk["hash"])):
                    raise ValueError("chunk verification failed")
                write_chunk(partial, int(chunk["offset"]), data)
                have.add(index)
                federation.set_have(job_id, have, total)
                transferred = sum(int(chunks[i]["length"]) for i in have if 0 <= i < len(chunks))
                federation.update_transfer(job_id, status="running", transferred_bytes=transferred, source_peer=peer_id, error="")
                federation.record_event(
                    "multisource_chunk_completed",
                    transfer_id=job_id,
                    peer_id=peer_id,
                    detail={"chunk": index, "transferred_bytes": transferred, "sources": len(discovered["sources"])},
                )
                break
            except Exception as exc:
                source_failures[peer_id] = source_failures.get(peer_id, 0) + 1
                candidates.pop(peer_id, None)
                federation.set_peer_health(peer_id, error=str(exc))
                federation.record_event(
                    "multisource_chunk_source_failed",
                    transfer_id=job_id,
                    peer_id=peer_id,
                    detail={"chunk": index, "error": str(exc)[:500]},
                )
    if not complete(have, total) or not verify_file(partial, digest):
        federation.update_transfer(job_id, status="failed", error="final blob verification failed")
        raise ValueError("complete multi-source blob could not be verified")
    final = federation.incoming / f"{digest}.blob"
    if final.exists() and not verify_file(final, digest):
        final.unlink()
    if partial != final:
        partial.replace(final)
    federation.update_transfer(job_id, status="complete", transferred_bytes=int(manifest.get("size", 0)), final_path=str(final), error="")
    federation.record_event(
        "multisource_transfer_completed",
        transfer_id=job_id,
        detail={"blob_hash": digest, "sources": sorted(discovered["sources"]), "source_failures": source_failures},
    )
    return {"transfer_id": job_id, "status": "complete", "path": str(final), "sources": discovered["details"], "source_failures": source_failures}


def repair_blob(root: str | Path, digest: str, *, peer_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """Verify a local blob and reconstruct it from peers only when necessary."""
    digest = normalize_sha256(digest)
    federation = FederationStore(root)
    local: Path | None = None
    try:
        local = _find_blob(root, digest)
    except ValueError:
        local = None
    if local is not None and verify_file(local, digest):
        federation.record_event("repair_verified", detail={"blob_hash": digest, "path": str(local)})
        return {"status": "verified", "blob_hash": digest, "path": str(local), "repaired": False}
    result = pull_blob_multisource(root, digest, peer_ids=peer_ids, operation="REPAIR", job_id=f"repair-{digest[:24]}")
    repaired = Path(result["path"])
    if local is not None:
        resolved = local.resolve()
        root_path = Path(root).expanduser().resolve()
        if root_path not in (resolved, *resolved.parents) or local.is_symlink():
            raise ValueError("refusing to replace unsafe local blob path")
        backup = federation.incoming / f"corrupt-{digest[:16]}-{int(time.time())}.bak"
        shutil.copy2(local, backup)
        shutil.copy2(repaired, local)
        if not verify_file(local, digest):
            shutil.copy2(backup, local)
            raise ValueError("repaired blob failed verification after replacement")
        federation.record_event("repair_replaced_corrupt_blob", detail={"blob_hash": digest, "path": str(local), "backup": str(backup)})
        result.update({"path": str(local), "backup": str(backup)})
    result["repaired"] = True
    return result


def rebalance_blob(root: str | Path, digest: str, *, desired_copies: int = 2) -> dict[str, Any]:
    """Ensure a verified blob has at least ``desired_copies`` configured remote copies."""
    digest = normalize_sha256(digest)
    desired_copies = max(1, min(int(desired_copies), 32))
    local = _find_blob(root, digest)
    if not verify_file(local, digest):
        raise ValueError("local source blob is corrupt; repair it before rebalancing")
    federation = FederationStore(root)
    candidates: list[str] = []
    existing: set[str] = set()
    errors: dict[str, str] = {}
    for peer in federation.list_peers():
        if not peer.get("enabled") or not _policy_allows_receive(peer, "documents"):
            continue
        peer_id = peer["peer_id"]
        candidates.append(peer_id)
        try:
            availability = remote_availability(root, peer_id, digest)
            count = int(availability.get("chunk_count", 0))
            if count == 0 or _availability_set(availability, count) == set(range(count)):
                existing.add(peer_id)
        except Exception as exc:
            errors[peer_id] = str(exc)[:500]
    targets = choose_replication_targets(candidates, existing, desired_copies)
    pushed: list[str] = []
    for peer_id in targets:
        job_id = transfer_id()
        manifest = __import__("app.federation_core", fromlist=["build_manifest"]).build_manifest(local)
        federation.create_transfer(
            job_id,
            direction="outgoing",
            operation="REPLICATE",
            blob_hash=digest,
            target_peer=peer_id,
            status="queued",
            total_bytes=int(manifest["size"]),
            total_chunks=int(manifest["chunk_count"]),
            manifest=manifest,
        )
        try:
            push_blob_to_peer(root, job_id)
            pushed.append(peer_id)
        except Exception as exc:
            errors[peer_id] = str(exc)[:500]
    federation.record_event("rebalance_completed", detail={"blob_hash": digest, "existing": sorted(existing), "pushed": pushed, "desired_copies": desired_copies, "errors": errors})
    return {"blob_hash": digest, "desired_copies": desired_copies, "existing": sorted(existing), "pushed": pushed, "errors": errors}


@bp.get("/tasks/<owner>/export.json")
def export_tasks(owner: str):
    owner = owner.strip()
    if not owner:
        return jsonify({"error": "owner_required"}), 400
    rows = TodoStore(current_app.config["DOCUMENT_ROOT"]).items(owner)
    body = json.dumps({"schema": "sofp-tasks/v1", "owner": owner, "tasks": rows}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(body) > MAX_RESOURCE_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    return Response(body, 200, {"Content-Type": "application/json; charset=utf-8", "X-Federation-Resource": "tasks"})


@bp.post("/tasks/<owner>/import.json")
def import_tasks(owner: str):
    data = request.get_data(cache=False)
    if len(data) > MAX_RESOURCE_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    try:
        payload = json.loads(data.decode("utf-8"))
        if payload.get("schema") != "sofp-tasks/v1":
            raise ValueError("unsupported task schema")
        tasks = payload.get("tasks") or []
        if not isinstance(tasks, list) or len(tasks) > 5000:
            raise ValueError("invalid task list")
        store = TodoStore(current_app.config["DOCUMENT_ROOT"])
        imported = updated = 0
        by_uid = {row.get("uid"): row for row in store.items(owner) if row.get("uid")}
        for incoming in tasks:
            if not isinstance(incoming, dict) or not str(incoming.get("title", "")).strip():
                continue
            uid = str(incoming.get("uid", "")).strip()
            current = by_uid.get(uid)
            values = dict(incoming)
            values.pop("id", None)
            values["uid"] = uid
            if current:
                if int(incoming.get("sequence", 0)) > int(current.get("sequence", 0)):
                    store.update(current["id"], values, owner); updated += 1
            else:
                created = store.add(str(incoming["title"]), owner, values); imported += 1
                if uid:
                    by_uid[uid] = created
        return jsonify({"resource": "tasks", "owner": owner, "imported": imported, "updated": updated})
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return jsonify({"error": "invalid_task_payload", "detail": str(exc)[:500]}), 400


@bp.get("/calendars/<owner>/export.ics")
def export_calendar(owner: str):
    body = CalendarStore(current_app.config["DOCUMENT_ROOT"]).export_ics(owner).encode("utf-8")
    if len(body) > MAX_RESOURCE_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    return Response(body, 200, {"Content-Type": "text/calendar; charset=utf-8", "X-Federation-Resource": "calendars"})


@bp.post("/calendars/<owner>/import.ics")
def import_calendar(owner: str):
    data = request.get_data(cache=False)
    if len(data) > MAX_RESOURCE_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    try:
        imported = CalendarStore(current_app.config["DOCUMENT_ROOT"]).import_ics(data.decode("utf-8"), owner)
    except (UnicodeDecodeError, ValueError) as exc:
        return jsonify({"error": "invalid_calendar_payload", "detail": str(exc)[:500]}), 400
    return jsonify({"resource": "calendars", "owner": owner, "imported": imported})


@click.command("federation-pull-multisource")
@click.argument("digest")
@click.option("--peer", "peer_ids", multiple=True, help="Restrict sources to configured peer IDs.")
def pull_multisource_command(digest: str, peer_ids: tuple[str, ...]) -> None:
    click.echo(json.dumps(pull_blob_multisource(current_app.config["DOCUMENT_ROOT"], digest, peer_ids=peer_ids), ensure_ascii=False))


@click.command("federation-repair")
@click.argument("digest")
@click.option("--peer", "peer_ids", multiple=True, help="Restrict repair sources to configured peer IDs.")
def repair_command(digest: str, peer_ids: tuple[str, ...]) -> None:
    click.echo(json.dumps(repair_blob(current_app.config["DOCUMENT_ROOT"], digest, peer_ids=peer_ids), ensure_ascii=False))


@click.command("federation-rebalance")
@click.argument("digest")
@click.option("--copies", default=2, type=click.IntRange(1, 32), show_default=True)
def rebalance_command(digest: str, copies: int) -> None:
    click.echo(json.dumps(rebalance_blob(current_app.config["DOCUMENT_ROOT"], digest, desired_copies=copies), ensure_ascii=False))


def init_app(app) -> None:
    app.register_blueprint(bp)
    app.cli.add_command(pull_multisource_command)
    app.cli.add_command(repair_command)
    app.cli.add_command(rebalance_command)
