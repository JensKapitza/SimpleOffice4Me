"""Outgoing SOFP HTTP worker for delegated and direct blob transfers."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .document_store import DocumentStore
from .federation_core import (
    build_manifest,
    bounded_parallelism,
    capability_allows_blob,
    manifest_chunk,
    normalize_sha256,
    retryable_status,
    transfer_progress,
    verify_chunk,
)
from .federation_store import FederationStore


USER_AGENT = "SimpleOffice4Me-SOFP/1"


def _request(url: str, *, method: str = "GET", token: str = "", body: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 30):
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    return urllib.request.urlopen(req, timeout=timeout)


def peer_capabilities(root: str | Path, peer_id: str) -> dict[str, Any]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("Federation-Peer ist nicht aktiv")
    token = store.peer_token(peer_id)
    try:
        with _request(peer["base_url"] + "/federation/v1/capabilities", token=token) as response:
            data = json.loads(response.read().decode("utf-8"))
        store.set_peer_health(peer_id, seen=True)
        return data
    except Exception as exc:
        store.set_peer_health(peer_id, error=str(exc))
        raise


def _find_blob(root: str | Path, digest: str) -> Path:
    digest = normalize_sha256(digest)
    documents = DocumentStore(root)
    documents.initialize()
    with documents._db() as db:
        row = db.execute(
            "SELECT relative_path FROM scan_file WHERE sha256=? ORDER BY relative_path LIMIT 1", (digest,)
        ).fetchone()
    if not row:
        raise ValueError("Blob nicht im lokalen Dokumentindex gefunden")
    path = (documents.root / str(row["relative_path"])).resolve()
    if documents.root not in (path, *path.parents) or not path.is_file() or path.is_symlink():
        raise ValueError("Lokaler Blob ist nicht freigegeben")
    return path


def push_blob_to_peer(root: str | Path, transfer_id: str) -> dict[str, Any]:
    store = FederationStore(root)
    transfer = store.get_transfer(transfer_id, include_secret=True)
    if not transfer:
        raise ValueError("Unbekannter Federation-Transfer")
    target_peer_id = transfer.get("target_peer", "")
    peer = store.get_peer(target_peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("Ziel-Peer ist nicht aktiv")
    token = store.peer_token(target_peer_id)
    path = _find_blob(root, transfer["blob_hash"])
    manifest = transfer.get("manifest") or build_manifest(path)
    if normalize_sha256(manifest["blob_hash"]) != normalize_sha256(transfer["blob_hash"]):
        raise ValueError("Transfer-Manifest passt nicht zum Blob")
    capability = transfer.get("capability", "")
    target_url = transfer.get("target_url") or peer["base_url"]
    total = int(manifest.get("size", 0))
    sent = int(transfer.get("transferred_bytes", 0))
    store.update_transfer(transfer_id, status="running", error="")
    try:
        for chunk in manifest.get("chunks", []):
            index = int(chunk["index"])
            start = int(chunk["offset"])
            length = int(chunk["length"])
            with path.open("rb") as source:
                source.seek(start)
                data = source.read(length)
            if not verify_chunk(data, chunk["hash"]):
                raise ValueError(f"Lokaler Chunk {index} ist korrupt")
            endpoint = f"{target_url.rstrip('/')}/federation/v1/transfers/{transfer_id}/chunks/{index}"
            headers = {
                "Content-Type": "application/octet-stream",
                "X-Chunk-SHA256": str(chunk["hash"]),
                "X-Blob-SHA256": transfer["blob_hash"],
                "X-Chunk-Offset": str(start),
                "X-Chunk-Length": str(length),
            }
            if capability:
                headers["X-Federation-Capability"] = capability
            with _request(endpoint, method="PUT", token=token, body=data, headers=headers, timeout=120) as response:
                if response.status not in (200, 201, 204):
                    raise ValueError(f"Ziel meldet HTTP {response.status}")
            sent += length
            store.update_transfer(transfer_id, transferred_bytes=sent)
        status_url = f"{target_url.rstrip('/')}/federation/v1/transfers/{transfer_id}/status"
        with _request(status_url, token=token, timeout=30) as response:
            remote = json.loads(response.read().decode("utf-8"))
        if remote.get("status") not in {"complete", "verified"}:
            raise ValueError(f"Zieltransfer nicht abgeschlossen: {remote.get('status', 'unknown')}")
        return store.update_transfer(transfer_id, status="complete", transferred_bytes=total, error="")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        store.update_transfer(transfer_id, status="failed", error=f"HTTP {exc.code}: {detail}")
        raise
    except Exception as exc:
        store.update_transfer(transfer_id, status="failed", error=str(exc)[:1000])
        raise


def transfer_summary(root: str | Path, transfer_id: str) -> dict[str, Any]:
    transfer = FederationStore(root).get_transfer(transfer_id)
    if not transfer:
        raise ValueError("Unbekannter Federation-Transfer")
    total = int(transfer.get("total_bytes", 0))
    done = int(transfer.get("transferred_bytes", 0))
    return {**transfer, "progress": transfer_progress(done, total)}
