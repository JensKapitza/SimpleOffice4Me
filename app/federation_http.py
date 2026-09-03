"""HTTP transport for resumable SOFP blob downloads and delegated uploads."""
from __future__ import annotations

import hmac
import os
import re
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from .document_store import DocumentStore, sha256_file
from .federation_core import (
    DEFAULT_CHUNK_SIZE,
    build_manifest,
    capability_summary,
    chunk_range,
    complete,
    normalize_sha256,
    preallocate,
    transfer_id,
    verify_chunk,
    verify_file,
    write_chunk,
)
from .federation_store import FederationStore

bp = Blueprint("federation_http", __name__, url_prefix="/federation/v1")
BLOCK_SIZE = 256 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


def _authorized() -> bool:
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not expected:
        return bool(current_app.testing)
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(expected, supplied)


@bp.before_request
def authenticate():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _safe_path(store: DocumentStore, relative: str) -> Path:
    path = (store.root / relative).resolve()
    if store.root not in (path, *path.parents) or not path.is_file() or path.is_symlink():
        raise ValueError("document unavailable")
    return path


def _document(document_id: str) -> tuple[dict, Path]:
    store = _store()
    item = store.get_document(document_id)
    return item, _safe_path(store, str(item.get("last_path", "")))


def _blob_path(digest: str) -> Path:
    digest = normalize_sha256(digest)
    store = _store()
    store.initialize()
    with store._db() as db:
        row = db.execute(
            "SELECT relative_path FROM scan_file WHERE sha256=? ORDER BY relative_path LIMIT 1",
            (digest,),
        ).fetchone()
    if row is None:
        raise ValueError("blob unavailable")
    return _safe_path(store, str(row["relative_path"]))


def _range(value: str, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    if "," in value:
        raise ValueError("multiple ranges unsupported")
    match = RANGE_RE.fullmatch(value.strip())
    if not match or size < 1:
        raise ValueError("invalid range")
    first, last = match.groups()
    if not first:
        length = int(last or "0")
        if length < 1:
            raise ValueError("invalid suffix")
        return max(0, size - length), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)


def _stream(path: Path, start: int, length: int):
    remaining = length
    with path.open("rb") as source:
        source.seek(start)
        while remaining:
            block = source.read(min(BLOCK_SIZE, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def _send(path: Path, digest: str, forced_range: tuple[int, int] | None = None) -> Response:
    size = path.stat().st_size
    digest = normalize_sha256(digest)
    etag = f'"sha256:{digest}"'
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "X-Content-SHA256": digest,
        "Cache-Control": "private, no-transform",
        "Content-Type": "application/octet-stream",
    }
    if request.headers.get("If-None-Match", "").strip() == etag and not request.headers.get("Range") and forced_range is None:
        return Response(status=304, headers=headers)
    try:
        selected = forced_range if forced_range is not None else _range(request.headers.get("Range", ""), size)
    except ValueError:
        headers["Content-Range"] = f"bytes */{size}"
        return Response(status=416, headers=headers)
    if selected is None:
        start, end, status = 0, max(0, size - 1), 200
    else:
        start, end, status = selected[0], selected[1], 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = 0 if size == 0 else end - start + 1
    headers["Content-Length"] = str(length)
    body = b"" if request.method == "HEAD" else stream_with_context(_stream(path, start, length))
    return Response(body, status, headers=headers, direct_passthrough=request.method != "HEAD")


@bp.get("/capabilities")
def capabilities():
    result = capability_summary()
    result.update({
        "transport": "http",
        "curl_resume": True,
        "resources": ["documents", "contacts", "calendars", "tasks"],
        "incoming_chunk_put": True,
        "persistent_transfers": True,
    })
    return jsonify(result)


@bp.route("/documents/<document_id>/manifest", methods=["GET", "HEAD"])
def manifest(document_id: str):
    try:
        item, path = _document(document_id)
    except (ValueError, KeyError):
        return jsonify({"error": "not_found"}), 404
    digest = str(item.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = sha256_file(path)
    return jsonify({
        "document_id": document_id,
        "blob_hash": f"sha256:{digest}",
        "size": path.stat().st_size,
        "accept_ranges": "bytes",
        "download": f"/federation/v1/documents/{document_id}/blob",
        "content_addressed_download": f"/federation/v1/blobs/{digest}",
    })


@bp.route("/documents/<document_id>/blob", methods=["GET", "HEAD"])
def document_blob(document_id: str):
    try:
        item, path = _document(document_id)
    except (ValueError, KeyError):
        return jsonify({"error": "not_found"}), 404
    digest = str(item.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = sha256_file(path)
    return _send(path, digest)


@bp.route("/blobs/<digest>", methods=["GET", "HEAD"])
def blob(digest: str):
    try:
        normalized = normalize_sha256(digest)
        path = _blob_path(normalized)
    except ValueError:
        return jsonify({"error": "not_found"}), 404
    return _send(path, normalized)


@bp.get("/blobs/<digest>/manifest")
def blob_manifest(digest: str):
    try:
        path = _blob_path(digest)
    except ValueError:
        return jsonify({"error": "not_found"}), 404
    result = build_manifest(path, DEFAULT_CHUNK_SIZE)
    if result["blob_hash"] != normalize_sha256(digest):
        return jsonify({"error": "index_hash_mismatch"}), 409
    result["chunk_download_template"] = f"/federation/v1/blobs/{result['blob_hash']}/chunks/{{index}}"
    return jsonify(result)


@bp.route("/blobs/<digest>/chunks/<int:index>", methods=["GET", "HEAD"])
def blob_chunk(digest: str, index: int):
    try:
        normalized = normalize_sha256(digest)
        path = _blob_path(normalized)
        start, end = chunk_range(index, path.stat().st_size, DEFAULT_CHUNK_SIZE)
    except (ValueError, IndexError):
        return jsonify({"error": "not_found"}), 404
    if start < 0 or end < start or start >= path.stat().st_size:
        return jsonify({"error": "chunk_not_found"}), 404
    return _send(path, normalized, (start, end))


@bp.get("/blobs/<digest>/availability")
def availability(digest: str):
    try:
        path = _blob_path(digest)
        result = build_manifest(path, DEFAULT_CHUNK_SIZE)
    except ValueError:
        return jsonify({"error": "not_found"}), 404
    return jsonify({
        "blob_hash": result["blob_hash"],
        "chunk_count": result["chunk_count"],
        "available": [[0, result["chunk_count"] - 1]] if result["chunk_count"] else [],
    })


@bp.post("/transfers/prepare")
def prepare_transfer():
    body = request.get_json(silent=True) or {}
    try:
        digest = normalize_sha256(body.get("blob_hash", ""))
        size = int(body.get("size", 0))
        result_manifest = body.get("manifest") or {}
        if size < 0 or size > int(current_app.config.get("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)) * 100:
            raise ValueError("invalid size")
        total_chunks = int(result_manifest.get("chunk_count", body.get("chunk_count", 0)))
        if total_chunks < 0 or total_chunks > 1_000_000:
            raise ValueError("invalid chunk count")
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_transfer"}), 400
    jobs = _federation()
    job_id = str(body.get("transfer_id") or transfer_id())[:160]
    if jobs.get_transfer(job_id):
        return jsonify({"error": "transfer_exists", "transfer_id": job_id}), 409
    partial = jobs.incoming / f"{job_id}.part"
    preallocate(partial, size)
    jobs.create_transfer(
        job_id,
        direction="incoming",
        operation=str(body.get("operation", "COPY")).upper(),
        blob_hash=digest,
        status="prepared",
        source_peer=str(body.get("source_peer", ""))[:128],
        total_bytes=size,
        total_chunks=total_chunks,
        manifest=result_manifest,
    )
    jobs.update_transfer(job_id, final_path=str(partial))
    return jsonify({
        "transfer_id": job_id,
        "status": "prepared",
        "chunk_upload_template": f"/federation/v1/transfers/{job_id}/chunks/{{index}}",
        "status_url": f"/federation/v1/transfers/{job_id}/status",
    }), 201


@bp.put("/transfers/<job_id>/chunks/<int:index>")
def receive_transfer_chunk(job_id: str, index: int):
    jobs = _federation()
    transfer = jobs.get_transfer(job_id)
    if not transfer or transfer.get("direction") != "incoming":
        return jsonify({"error": "transfer_not_found"}), 404
    if transfer.get("status") in {"complete", "cancelled"}:
        return jsonify({"error": "transfer_closed"}), 409
    result_manifest = transfer.get("manifest") or {}
    chunks = result_manifest.get("chunks") or []
    if index < 0 or index >= len(chunks):
        return jsonify({"error": "chunk_not_found"}), 404
    chunk = chunks[index]
    data = request.get_data(cache=False)
    expected_length = int(chunk.get("length", -1))
    expected_hash = str(chunk.get("hash", ""))
    if len(data) != expected_length:
        return jsonify({"error": "wrong_length"}), 400
    try:
        if not verify_chunk(data, expected_hash):
            return jsonify({"error": "hash_mismatch"}), 409
    except ValueError:
        return jsonify({"error": "invalid_manifest_hash"}), 409
    target = Path(str(transfer.get("final_path") or ""))
    if jobs.incoming.resolve() not in (target.resolve(), *target.resolve().parents):
        return jsonify({"error": "unsafe_target"}), 409
    write_chunk(target, int(chunk.get("offset", 0)), data)
    have = jobs.have(job_id)
    have.add(index)
    jobs.set_have(job_id, have, int(transfer.get("total_chunks", len(chunks))))
    transferred = sum(int(chunks[i].get("length", 0)) for i in have if i < len(chunks))
    jobs.update_transfer(job_id, status="receiving", transferred_bytes=transferred)
    if complete(have, int(transfer.get("total_chunks", len(chunks)))):
        if not verify_file(target, transfer["blob_hash"]):
            jobs.update_transfer(job_id, status="failed", error="final hash mismatch")
            return jsonify({"error": "final_hash_mismatch"}), 409
        final = jobs.incoming / f"{transfer['blob_hash']}.blob"
        target.replace(final)
        jobs.update_transfer(job_id, status="complete", transferred_bytes=int(transfer.get("total_bytes", 0)), final_path=str(final), error="")
    current = jobs.get_transfer(job_id) or {}
    return jsonify({
        "transfer_id": job_id,
        "chunk": index,
        "status": current.get("status"),
        "transferred_bytes": current.get("transferred_bytes", 0),
        "total_bytes": current.get("total_bytes", 0),
    })


@bp.get("/transfers/<job_id>/status")
def transfer_status(job_id: str):
    transfer = _federation().get_transfer(job_id)
    if not transfer:
        return jsonify({"error": "transfer_not_found"}), 404
    transfer.pop("capability_enc", None)
    transfer.pop("final_path", None)
    return jsonify(transfer)
