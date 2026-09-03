"""Outgoing SOFP HTTP workers for direct and delegated blob transfers."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .document_store import DocumentStore
from .federation_core import (
    bitmap_decode,
    build_manifest,
    normalize_sha256,
    transfer_progress,
    verify_chunk,
)
from .federation_store import FederationStore


USER_AGENT = "SimpleOffice4Me-SOFP/1"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Federation requests never follow redirects implicitly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _request(
    url: str,
    *,
    method: str = "GET",
    token: str = "",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Ungültige Federation-Zieladresse")
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    return _OPENER.open(req, timeout=timeout)


def _json_request(
    url: str,
    *,
    method: str = "GET",
    token: str = "",
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else None
    with _request(url, method=method, token=token, body=body, headers=headers, timeout=timeout) as response:
        raw = response.read()
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Federation-Antwort ist kein JSON-Objekt")
    return value


def validate_transient_target(base_url: str) -> str:
    """Validate an orchestrator-supplied transient sink without permitting URL tricks.

    Private RFC1918/ULA targets are intentionally allowed because SOFP is designed
    for trusted LAN/VPN federation. Loopback, link-local, multicast and unspecified
    destinations are rejected unless explicitly enabled for local integration tests.
    """
    value = str(base_url or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Delegiertes Ziel muss HTTP oder HTTPS verwenden")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Delegiertes Ziel darf keine Credentials, Query oder Fragmente enthalten")
    if parsed.path not in {"", "/"}:
        raise ValueError("Delegiertes Ziel muss eine Server-Basis-URL sein")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    allow_loopback = os.environ.get("SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Delegiertes Ziel kann nicht aufgelöst werden") from exc
    if not addresses:
        raise ValueError("Delegiertes Ziel besitzt keine auflösbare Adresse")
    import ipaddress

    for entry in addresses:
        ip = ipaddress.ip_address(entry[4][0])
        if ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError("Delegiertes Ziel verwendet eine unzulässige Netzwerkadresse")
        if ip.is_loopback and not allow_loopback:
            raise ValueError("Loopback ist für delegierte Federation deaktiviert")
    return value


def peer_capabilities(root: str | Path, peer_id: str) -> dict[str, Any]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("Federation-Peer ist nicht aktiv")
    token = store.peer_token(peer_id)
    try:
        data = _json_request(peer["base_url"] + "/federation/v1/capabilities", token=token)
        store.set_peer_health(peer_id, seen=True)
        return data
    except Exception as exc:
        store.set_peer_health(peer_id, error=str(exc))
        raise


def remote_blob_manifest(root: str | Path, peer_id: str, digest: str) -> dict[str, Any]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("Quell-Peer ist nicht aktiv")
    digest = normalize_sha256(digest)
    result = _json_request(
        f"{peer['base_url']}/federation/v1/blobs/{digest}/manifest",
        token=store.peer_token(peer_id),
    )
    if normalize_sha256(result.get("blob_hash", "")) != digest:
        raise ValueError("Quell-Peer lieferte ein Manifest für einen anderen Blob")
    return result


def remote_availability(root: str | Path, peer_id: str, digest: str) -> dict[str, Any]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("Quell-Peer ist nicht aktiv")
    digest = normalize_sha256(digest)
    return _json_request(
        f"{peer['base_url']}/federation/v1/blobs/{digest}/availability",
        token=store.peer_token(peer_id),
    )


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


def _remote_status(base_url: str, transfer_id: str, token: str) -> dict[str, Any] | None:
    try:
        return _json_request(
            f"{base_url.rstrip('/')}/federation/v1/transfers/{transfer_id}/status",
            token=token,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _prepare_remote(
    base_url: str,
    token: str,
    transfer: dict[str, Any],
    manifest: dict[str, Any],
    *,
    delegated: bool = False,
    source_peer: str = "",
) -> dict[str, Any]:
    existing = _remote_status(base_url, transfer["transfer_id"], token)
    if existing is not None:
        return existing
    payload = {
        "transfer_id": transfer["transfer_id"],
        "operation": transfer.get("operation", "COPY"),
        "blob_hash": transfer["blob_hash"],
        "size": int(manifest.get("size", 0)),
        "chunk_count": int(manifest.get("chunk_count", 0)),
        "manifest": manifest,
        "delegated": bool(delegated),
        "source_peer": source_peer,
    }
    return _json_request(
        f"{base_url.rstrip('/')}/federation/v1/transfers/prepare",
        method="POST",
        token=token,
        payload=payload,
        timeout=60,
    )


def _remote_have(remote: dict[str, Any] | None, total_chunks: int) -> set[int]:
    if not remote:
        return set()
    try:
        return bitmap_decode(str(remote.get("have_bitmap") or ""), total_chunks)
    except ValueError:
        return set()


def _upload_transfer(
    root: str | Path,
    transfer: dict[str, Any],
    *,
    target_url: str,
    target_token: str,
    prepare: bool,
    prepare_peer_label: str = "",
) -> dict[str, Any]:
    store = FederationStore(root)
    transfer_id = transfer["transfer_id"]
    path = _find_blob(root, transfer["blob_hash"])
    manifest = transfer.get("manifest") or build_manifest(path)
    if normalize_sha256(manifest["blob_hash"]) != normalize_sha256(transfer["blob_hash"]):
        raise ValueError("Transfer-Manifest passt nicht zum Blob")
    total_chunks = int(manifest.get("chunk_count", 0))
    remote = _prepare_remote(
        target_url,
        target_token,
        transfer,
        manifest,
        delegated=False,
        source_peer=prepare_peer_label,
    ) if prepare else _remote_status(target_url, transfer_id, target_token)
    if remote is None:
        raise ValueError("Zieltransfer wurde nicht vorbereitet")
    have = _remote_have(remote, total_chunks)
    allowed = manifest.get("_allowed_chunks")
    allowed_chunks = set(int(value) for value in allowed) if isinstance(allowed, list) else set(range(total_chunks))
    chunks = manifest.get("chunks") or []
    sent = sum(int(chunks[i].get("length", 0)) for i in have if 0 <= i < len(chunks))
    store.update_transfer(transfer_id, status="running", transferred_bytes=sent, error="")
    try:
        for chunk in chunks:
            index = int(chunk["index"])
            if index not in allowed_chunks or index in have:
                continue
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
            with _request(endpoint, method="PUT", token=target_token, body=data, headers=headers, timeout=120) as response:
                if response.status not in (200, 201, 204):
                    raise ValueError(f"Ziel meldet HTTP {response.status}")
                payload = json.loads(response.read().decode("utf-8") or "{}")
            have.add(index)
            sent = max(sent, int(payload.get("transferred_bytes", 0)))
            store.update_transfer(transfer_id, transferred_bytes=sent)
        remote = _remote_status(target_url, transfer_id, target_token) or {}
        remote_status = str(remote.get("status", "unknown"))
        local_status = "complete" if remote_status in {"complete", "verified"} else "partial"
        transferred = int(remote.get("transferred_bytes", sent))
        return store.update_transfer(transfer_id, status=local_status, transferred_bytes=transferred, error="")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        store.update_transfer(transfer_id, status="failed", error=f"HTTP {exc.code}: {detail}")
        raise
    except Exception as exc:
        store.update_transfer(transfer_id, status="failed", error=str(exc)[:1000])
        raise


def push_blob_to_peer(root: str | Path, transfer_id: str) -> dict[str, Any]:
    """Push a local blob to a permanently configured peer, preparing it first."""
    store = FederationStore(root)
    transfer = store.get_transfer(transfer_id, include_secret=True)
    if not transfer:
        raise ValueError("Unbekannter Federation-Transfer")
    target_peer_id = str(transfer.get("target_peer") or "")
    peer = store.get_peer(target_peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("Ziel-Peer ist nicht aktiv")
    return _upload_transfer(
        root,
        transfer,
        target_url=peer["base_url"],
        target_token=store.peer_token(target_peer_id),
        prepare=True,
    )


def push_blob_to_transient_target(root: str | Path, transfer_id: str) -> dict[str, Any]:
    """Push a delegated blob to a one-transfer target using only its capability."""
    store = FederationStore(root)
    transfer = store.get_transfer(transfer_id, include_secret=True)
    if not transfer:
        raise ValueError("Unbekannter delegierter Transfer")
    target_url = validate_transient_target(str(transfer.get("target_url") or ""))
    capability = str(transfer.get("capability") or "")
    if not capability:
        raise ValueError("Delegierter Transfer besitzt keine Ziel-Capability")
    return _upload_transfer(
        root,
        transfer,
        target_url=target_url,
        target_token=capability,
        prepare=False,
    )


def transfer_summary(root: str | Path, transfer_id: str) -> dict[str, Any]:
    transfer = FederationStore(root).get_transfer(transfer_id)
    if not transfer:
        raise ValueError("Unbekannter Federation-Transfer")
    total = int(transfer.get("total_bytes", 0))
    done = int(transfer.get("transferred_bytes", 0))
    return {**transfer, "progress": transfer_progress(done, total)}
