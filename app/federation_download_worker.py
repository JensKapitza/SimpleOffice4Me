"""Priority-aware resumable pull pipeline for remote SOFP documents."""
from __future__ import annotations

import logging
import re
import shutil
import time
import urllib.error
from pathlib import Path
from typing import Any

from .document_store import DocumentStore, sha256_file
from .federation_blocks import FederationBlockStore, content_manifest_valid, normalize_sha512, sha512_bytes
from .federation_catalog import FederationCatalog
from .federation_core import complete, preallocate, verify_chunk, verify_file, write_chunk
from .federation_store import FederationStore
from .federation_worker import _json_request, _request, remote_blob_manifest


logger = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)


def _peer(root: str | Path, peer_id: str) -> tuple[FederationStore, dict[str, Any], str]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Federation-Peer ist nicht aktiv")
    return store, peer, store.peer_token(peer_id)


def _defer_request(catalog: FederationCatalog, request_row: dict[str, Any], exc: Exception, *, attempts: int) -> dict[str, Any]:
    delay = min(3600, 15 * (2 ** min(max(0, attempts - 1), 8)))
    status = "waiting_peer" if isinstance(exc, _NETWORK_ERRORS) else "retry"
    updated = catalog.update_request(
        request_row["request_id"], status=status, attempts=attempts, last_error=str(exc)[:1000],
        next_attempt_at=int(time.time()) + delay,
    )
    catalog.record_event(
        "download_deferred", request_id=request_row["request_id"], peer_id=request_row["peer_id"],
        detail={"status": status, "retry_in": delay, "attempts": attempts, "error": str(exc)[:500]},
    )
    logger.warning(
        "federation download deferred request=%s peer=%s status=%s attempts=%s retry_in=%s error=%s",
        request_row["request_id"], request_row["peer_id"], status, attempts, delay, str(exc)[:300],
    )
    return updated


def sync_peer_catalog(root: str | Path, peer_id: str, *, page_size: int = 500) -> dict[str, Any]:
    """Copy a peer's document index locally so it remains browsable while offline."""
    federation, peer, token = _peer(root, peer_id)
    catalog = FederationCatalog(root)
    cursor = 0
    generation = ""
    imported = 0
    logger.info("federation catalog sync started peer=%s", peer_id)
    try:
        while True:
            page = _json_request(
                f"{peer['base_url']}/federation/v1/catalog/documents?cursor={cursor}&limit={max(1, min(page_size, 1000))}",
                token=token, timeout=60,
            )
            if page.get("schema") != "sofp-document-index/v1":
                raise ValueError("Peer liefert keinen kompatiblen Dokumentindex")
            page_generation = str(page.get("generation") or "")
            if not page_generation:
                raise ValueError("Remote-Index besitzt keine Generation")
            if generation and generation != page_generation:
                cursor = 0; imported = 0; generation = page_generation
                catalog.begin_index(peer_id, generation)
                continue
            if not generation:
                generation = page_generation
                catalog.begin_index(peer_id, generation)
            documents = page.get("documents") or []
            if not isinstance(documents, list):
                raise ValueError("Remote-Index enthält keine Dokumentliste")
            imported += catalog.ingest(peer_id, generation, [row for row in documents if isinstance(row, dict)])
            next_cursor = page.get("next_cursor")
            if next_cursor is None:
                break
            cursor = int(next_cursor)
        catalog.finish_index(peer_id, generation)
        federation.set_peer_health(peer_id, seen=True)
        federation.record_event("catalog_synced", peer_id=peer_id, detail={"generation": generation, "documents": imported})
        return {"peer_id": peer_id, "generation": generation, "documents": imported}
    except Exception as exc:
        catalog.fail_index(peer_id, str(exc))
        federation.set_peer_health(peer_id, error=str(exc))
        logger.exception("federation catalog sync failed peer=%s", peer_id)
        raise


def _safe_destination(root: Path, peer_id: str, remote: dict[str, Any], request_id: str) -> Path:
    peer_component = _SAFE_NAME.sub("-", peer_id).strip("-.")[:80] or "peer"
    original_name = Path(str(remote.get("path") or "remote-file")).name
    name = _SAFE_NAME.sub("-", original_name).strip("-.")[:180] or "remote-file"
    remote_id = _SAFE_NAME.sub("-", str(remote.get("remote_document_id") or "document")).strip("-.")[:80]
    folder = root / "Federation" / peer_component
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ValueError("Federation-Zielordner darf kein Symlink sein")
    candidate = folder / f"{remote_id}-{name}"
    if candidate.exists() and candidate.is_file() and sha256_file(candidate) != remote["blob_hash"]:
        candidate = folder / f"{request_id[:12]}-{remote_id}-{name}"
    resolved = candidate.resolve()
    if root not in (resolved, *resolved.parents):
        raise ValueError("Federation-Ziel liegt außerhalb des Dokumentenspeichers")
    return candidate


def _finalize_document(root: str | Path, request_row: dict[str, Any], partial: Path) -> dict[str, Any]:
    catalog = FederationCatalog(root)
    remote = catalog.get_remote(request_row["peer_id"], request_row["remote_document_id"])
    if not remote:
        raise ValueError("Remote-Datei ist nicht mehr im lokalen Katalog")
    documents = DocumentStore(root)
    destination = _safe_destination(documents.root, request_row["peer_id"], remote, request_row["request_id"])
    if not destination.exists():
        shutil.copy2(partial, destination)
    elif sha256_file(destination) != remote["blob_hash"]:
        raise ValueError("Lokale Zieldatei kollidiert mit einem anderen Inhalt")
    documents.initialize()
    documents._scan_file(destination.resolve(), force_hash=True)
    metadata = documents.get_document(destination)
    actor = f"federation:{request_row['peer_id']}"
    tags = sorted({
        *metadata.get("tags", []), *remote.get("tags", []), *remote.get("origin_tags", []),
        "source:federation", f"federation-peer:{request_row['peer_id']}",
        f"federation-document:{request_row['remote_document_id']}",
    }, key=str.casefold)
    metadata = documents.update_metadata(
        metadata["document_id"], tags=tags,
        attributes={"federation_origin": {
            "peer_id": request_row["peer_id"], "remote_document_id": request_row["remote_document_id"],
            "remote_path": remote.get("path", ""), "blob_hash": remote["blob_hash"],
            "origin_tags": remote.get("origin_tags", []),
        }}, author=actor,
    )
    partial.unlink(missing_ok=True)
    return metadata


def _remote_content_manifest(peer: dict[str, Any], token: str, blob_hash: str) -> dict[str, Any] | None:
    """Return the optional content-defined block manifest; old peers simply fall back."""
    try:
        manifest = _json_request(
            f"{peer['base_url']}/federation/v1/blocks/blobs/{blob_hash}/manifest",
            token=token, timeout=120,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 405, 501}:
            return None
        raise
    if not content_manifest_valid(manifest):
        return None
    return manifest


def _deduplicated_download(
    root: str | Path,
    request_row: dict[str, Any],
    peer: dict[str, Any],
    token: str,
    federation: FederationStore,
) -> Path | None:
    """Build a file from local blocks first, downloading only missing SHA-512 blocks."""
    manifest = _remote_content_manifest(peer, token, request_row["blob_hash"])
    if manifest is None:
        return None
    block_store = FederationBlockStore(root)
    # Incrementally index managed files. Existing unchanged manifests are cheap.
    index_result = block_store.index_documents()
    hashes = [normalize_sha512(block["sha512"]) for block in manifest["blocks"]]
    local = block_store.available(hashes)
    partial = federation.incoming / f"pull-blocks-{request_row['request_id']}.part"
    preallocate(partial, int(manifest["size"]))
    local_bytes = network_bytes = local_blocks = network_blocks = 0
    with partial.open("r+b") as target:
        for block in manifest["blocks"]:
            digest = normalize_sha512(block["sha512"])
            length = int(block["length"])
            if digest in local:
                data = block_store.read_block(digest, length)
                local_blocks += 1; local_bytes += length
            else:
                endpoint = f"{peer['base_url']}/federation/v1/blocks/{digest}"
                with _request(endpoint, token=token, timeout=120) as response:
                    data = response.read(length + 1)
                if len(data) != length or sha512_bytes(data) != digest:
                    raise ValueError("Remote-SHA-512-Block ist ungültig")
                block_store.put_cached_block(digest, data)
                local.add(digest)
                network_blocks += 1; network_bytes += length
            target.seek(int(block["offset"]))
            target.write(data)
    whole = __import__("hashlib").sha512()
    with partial.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            whole.update(data)
    if whole.hexdigest() != normalize_sha512(manifest["file_sha512"]):
        partial.unlink(missing_ok=True)
        raise ValueError("SHA-512-Rekonstruktion der Datei fehlgeschlagen")
    if not verify_file(partial, request_row["blob_hash"]):
        partial.unlink(missing_ok=True)
        raise ValueError("SHA-256-Endprüfung der rekonstruierten Datei fehlgeschlagen")
    federation.record_event(
        "content_blocks_reused", transfer_id=request_row["request_id"], peer_id=request_row["peer_id"],
        detail={
            "local_blocks": local_blocks, "network_blocks": network_blocks,
            "local_bytes": local_bytes, "network_bytes": network_bytes,
            "total_bytes": int(manifest["size"]), "indexed": index_result,
        },
    )
    logger.info(
        "federation block dedupe request=%s local_bytes=%s network_bytes=%s local_blocks=%s network_blocks=%s",
        request_row["request_id"], local_bytes, network_bytes, local_blocks, network_blocks,
    )
    return partial


def _legacy_chunk_download(
    root: str | Path,
    request_row: dict[str, Any],
    peer: dict[str, Any],
    token: str,
    federation: FederationStore,
    manifest: dict[str, Any],
) -> Path:
    request_id = request_row["request_id"]
    total_chunks = int(manifest.get("chunk_count", 0))
    transfer = federation.get_transfer(request_id)
    partial = federation.incoming / f"pull-{request_id}.part"
    if not transfer:
        preallocate(partial, int(manifest.get("size", 0)))
        federation.create_transfer(
            request_id, direction="incoming-pull", operation="COPY", blob_hash=request_row["blob_hash"],
            source_peer=request_row["peer_id"], status="running", total_bytes=int(manifest.get("size", 0)),
            total_chunks=total_chunks, manifest=manifest,
        )
        federation.update_transfer(request_id, final_path=str(partial))
    else:
        partial = Path(str(transfer.get("final_path") or partial))
        if not partial.exists():
            preallocate(partial, int(manifest.get("size", 0)))
            federation.update_transfer(request_id, final_path=str(partial), transferred_bytes=0, have_bitmap="")
    have = federation.have(request_id)
    chunks = manifest.get("chunks") or []
    for chunk in chunks:
        index = int(chunk["index"])
        if index in have:
            continue
        endpoint = f"{peer['base_url']}/federation/v1/blobs/{request_row['blob_hash']}/chunks/{index}"
        with _request(endpoint, token=token, timeout=120) as response:
            data = response.read(int(chunk["length"]) + 1)
        if len(data) != int(chunk["length"]) or not verify_chunk(data, str(chunk["hash"])):
            raise ValueError(f"Chunk {index} ist ungültig")
        write_chunk(partial, int(chunk["offset"]), data)
        have.add(index)
        federation.set_have(request_id, have, total_chunks)
        transferred = sum(int(chunks[i]["length"]) for i in have if 0 <= i < len(chunks))
        federation.update_transfer(request_id, status="running", transferred_bytes=transferred)
        federation.record_event(
            "download_chunk_completed", transfer_id=request_id, peer_id=request_row["peer_id"],
            detail={"chunk": index, "transferred_bytes": transferred},
        )
    if not complete(have, total_chunks) or not verify_file(partial, request_row["blob_hash"]):
        raise ValueError("Vollständige Datei konnte nicht verifiziert werden")
    return partial


def process_download(root: str | Path, request_id: str) -> dict[str, Any]:
    """Download one queued item, preferring local reusable blocks over network I/O."""
    catalog = FederationCatalog(root)
    request_row = catalog.get_request(request_id)
    if not request_row:
        raise ValueError("Download-Anforderung ist unbekannt")
    if request_row["status"] == "complete":
        return request_row
    attempts = int(request_row.get("attempts", 0)) + 1
    try:
        federation, peer, token = _peer(root, request_row["peer_id"])
        legacy_manifest = remote_blob_manifest(root, request_row["peer_id"], request_row["blob_hash"])
    except Exception as exc:
        _defer_request(catalog, request_row, exc, attempts=attempts)
        raise
    catalog.update_request(request_id, status="running", attempts=attempts, last_error="", next_attempt_at=0)
    federation.record_event(
        "download_started", transfer_id=request_id, peer_id=request_row["peer_id"],
        detail={"effective_priority": request_row.get("effective_priority", 0), "attempts": attempts},
    )
    try:
        partial = _deduplicated_download(root, request_row, peer, token, federation)
        if partial is None:
            partial = _legacy_chunk_download(root, request_row, peer, token, federation, legacy_manifest)
        metadata = _finalize_document(root, request_row, partial)
        federation.update_transfer(
            request_id, status="complete", transferred_bytes=int(legacy_manifest.get("size", 0)), error="",
        )
        result = catalog.update_request(
            request_id, status="complete", last_error="", next_attempt_at=0, local_document_id=metadata["document_id"],
        )
        catalog.record_event(
            "download_completed", request_id=request_id, peer_id=request_row["peer_id"],
            detail={"local_document_id": metadata["document_id"], "path": metadata.get("last_path", "")},
        )
        federation.record_event(
            "download_imported", transfer_id=request_id, peer_id=request_row["peer_id"],
            detail={"local_document_id": metadata["document_id"], "origin": request_row["remote_document_id"]},
        )
        # Make newly received blocks immediately available to later files.
        try:
            FederationBlockStore(root).manifest_for_file(Path(root) / metadata["last_path"], force=True)
        except (OSError, ValueError):
            logger.exception("federation block index refresh failed document=%s", metadata["document_id"])
        return result
    except Exception as exc:
        _defer_request(catalog, catalog.get_request(request_id) or request_row, exc, attempts=attempts)
        if federation.get_transfer(request_id):
            federation.update_transfer(request_id, status="paused", error=str(exc)[:1000])
        federation.record_event(
            "download_paused", transfer_id=request_id, peer_id=request_row["peer_id"],
            detail={"attempts": attempts, "error": str(exc)[:500]},
        )
        raise


def process_queue(root: str | Path, *, limit: int = 10) -> dict[str, Any]:
    """Process highest-priority eligible requests, re-evaluating priority each selection."""
    catalog = FederationCatalog(root)
    completed, deferred, failed = [], [], []
    handled: set[str] = set()
    for _ in range(max(1, min(int(limit), 100))):
        candidates = [row for row in catalog.next_requests(25) if row["request_id"] not in handled]
        if not candidates:
            break
        row = candidates[0]
        handled.add(row["request_id"])
        try:
            completed.append(process_download(root, row["request_id"]))
        except _NETWORK_ERRORS as exc:
            deferred.append({"request_id": row["request_id"], "error": str(exc)})
        except Exception as exc:
            failed.append({"request_id": row["request_id"], "error": str(exc)})
    return {"completed": completed, "deferred": deferred, "failed": failed}
