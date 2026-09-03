"""A -> B -> C orchestration for SOFP third-party blob transfers."""
from __future__ import annotations

import urllib.error
from pathlib import Path
from typing import Any

from .federation_core import normalize_sha256, transfer_id, validate_operation
from .federation_store import FederationStore
from .federation_worker import _json_request, peer_capabilities, remote_blob_manifest


def _active_peer(store: FederationStore, peer_id: str, role: str) -> dict[str, Any]:
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError(f"{role}-Peer ist nicht aktiv")
    return peer


def orchestrate_third_party(
    root: str | Path,
    source_peer_id: str,
    target_peer_id: str,
    digest: str,
    *,
    operation: str = "COPY",
) -> dict[str, Any]:
    """Coordinate a source peer B pushing directly to target peer C.

    Node A authenticates independently to B and C. C returns a short-lived,
    transfer-scoped capability. Only that capability and C's base URL are given
    to B, so B never learns A's permanent credential for C.
    """
    if source_peer_id == target_peer_id:
        raise ValueError("Quell- und Ziel-Peer müssen verschieden sein")
    digest = normalize_sha256(digest)
    operation = validate_operation(operation)
    store = FederationStore(root)
    source = _active_peer(store, source_peer_id, "Quell")
    target = _active_peer(store, target_peer_id, "Ziel")

    source_caps = peer_capabilities(root, source_peer_id)
    target_caps = peer_capabilities(root, target_peer_id)
    if not source_caps.get("delegated_push"):
        raise ValueError("Quell-Peer unterstützt keinen delegierten Push")
    if not target_caps.get("incoming_chunk_put") or not target_caps.get("transfer_capabilities"):
        raise ValueError("Ziel-Peer unterstützt keine delegierten Chunk-Transfers")

    manifest = remote_blob_manifest(root, source_peer_id, digest)
    job_id = transfer_id()
    store.create_transfer(
        job_id,
        direction="orchestrated",
        operation=operation,
        blob_hash=digest,
        status="preparing",
        source_peer=source_peer_id,
        target_peer=target_peer_id,
        total_bytes=int(manifest.get("size", 0)),
        total_chunks=int(manifest.get("chunk_count", 0)),
        manifest=manifest,
    )
    store.record_event(
        "orchestration_started",
        transfer_id=job_id,
        detail={"source_peer": source_peer_id, "target_peer": target_peer_id},
    )

    try:
        prepared = _json_request(
            target["base_url"] + "/federation/v1/transfers/prepare",
            method="POST",
            token=store.peer_token(target_peer_id),
            payload={
                "transfer_id": job_id,
                "operation": operation,
                "blob_hash": digest,
                "size": int(manifest.get("size", 0)),
                "chunk_count": int(manifest.get("chunk_count", 0)),
                "manifest": manifest,
                "delegated": True,
                "source_peer": source_peer_id,
            },
            timeout=60,
        )
        capability = str(prepared.get("transfer_capability") or "")
        if len(capability) < 24:
            raise ValueError("Ziel-Peer hat keine Transfer-Capability geliefert")
        store.update_transfer(job_id, status="delegated")

        result = _json_request(
            source["base_url"] + "/federation/v1/delegations/push",
            method="POST",
            token=store.peer_token(source_peer_id),
            payload={
                "transfer_id": job_id,
                "operation": operation,
                "blob_hash": digest,
                "target_url": target["base_url"],
                "target_capability": capability,
                "manifest": manifest,
            },
            timeout=60 * 60,
        )
        remote = _json_request(
            target["base_url"] + f"/federation/v1/transfers/{job_id}/status",
            token=store.peer_token(target_peer_id),
            timeout=30,
        )
        if remote.get("status") not in {"complete", "verified"}:
            raise ValueError(f"Ziel meldet Transferstatus {remote.get('status', 'unknown')}")
        transferred = int(remote.get("transferred_bytes", manifest.get("size", 0)))
        final = store.update_transfer(job_id, status="complete", transferred_bytes=transferred, error="")
        store.record_event(
            "orchestration_completed",
            transfer_id=job_id,
            detail={"source_status": result.get("status"), "target_status": remote.get("status")},
        )
        return final
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        store.update_transfer(job_id, status="failed", error=f"HTTP {exc.code}: {detail}")
        store.record_event("orchestration_failed", transfer_id=job_id, detail={"error": f"HTTP {exc.code}"})
        raise
    except Exception as exc:
        store.update_transfer(job_id, status="failed", error=str(exc)[:1000])
        store.record_event("orchestration_failed", transfer_id=job_id, detail={"error": str(exc)[:500]})
        raise
