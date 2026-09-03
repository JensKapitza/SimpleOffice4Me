"""HTTP transport for resumable SOFP blob downloads."""
from __future__ import annotations

import hmac
import os
import re
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from .document_store import DocumentStore, sha256_file
from .federation_core import DEFAULT_CHUNK_SIZE, build_manifest, capability_summary, chunk_range, normalize_sha256

bp = Blueprint("federation_http", __name__, url_prefix="/federation/v1")
BLOCK_SIZE = 256 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _store() -> DocumentStore: return DocumentStore(current_app.config["DOCUMENT_ROOT"])
def _authorized() -> bool:
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not expected: return bool(current_app.testing)
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(expected, supplied)

@bp.before_request
def authenticate():
    if not _authorized(): return Response("federation authentication required\n", 401, {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"})
    return None

def _safe_path(store: DocumentStore, relative: str) -> Path:
    path = (store.root / relative).resolve()
    if store.root not in (path, *path.parents) or not path.is_file() or path.is_symlink(): raise ValueError("document unavailable")
    return path

def _document(document_id: str) -> tuple[dict, Path]:
    store = _store(); item = store.get_document(document_id); return item, _safe_path(store, str(item.get("last_path", "")))
def _blob_path(digest: str) -> Path:
    digest = normalize_sha256(digest); store = _store(); store.initialize()
    with store._db() as db: row = db.execute("SELECT relative_path FROM scan_file WHERE sha256=? ORDER BY relative_path LIMIT 1", (digest,)).fetchone()
    if row is None: raise ValueError("blob unavailable")
    return _safe_path(store, str(row["relative_path"]))
def _range(value: str, size: int) -> tuple[int, int] | None:
    if not value: return None
    if "," in value: raise ValueError("multiple ranges unsupported")
    match = RANGE_RE.fullmatch(value.strip())
    if not match or size < 1: raise ValueError("invalid range")
    first, last = match.groups()
    if not first:
        length = int(last or "0")
        if length < 1: raise ValueError("invalid suffix")
        return max(0, size - length), size - 1
    start = int(first); end = int(last) if last else size - 1
    if start >= size or end < start: raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)
def _stream(path: Path, start: int, length: int):
    remaining = length
    with path.open("rb") as source:
        source.seek(start)
        while remaining:
            block = source.read(min(BLOCK_SIZE, remaining))
            if not block: break
            remaining -= len(block); yield block
def _send(path: Path, digest: str, forced_range: tuple[int, int] | None = None) -> Response:
    size = path.stat().st_size; digest = normalize_sha256(digest); etag = f'"sha256:{digest}"'
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "X-Content-SHA256": digest, "Cache-Control": "private, no-transform", "Content-Type": "application/octet-stream"}
    if request.headers.get("If-None-Match", "").strip() == etag and not request.headers.get("Range") and forced_range is None: return Response(status=304, headers=headers)
    try: selected = forced_range if forced_range is not None else _range(request.headers.get("Range", ""), size)
    except ValueError:
        headers["Content-Range"] = f"bytes */{size}"; return Response(status=416, headers=headers)
    if selected is None: start, end, status = 0, max(0, size - 1), 200
    else: start, end, status = selected[0], selected[1], 206; headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = 0 if size == 0 else end - start + 1; headers["Content-Length"] = str(length)
    body = b"" if request.method == "HEAD" else stream_with_context(_stream(path, start, length))
    return Response(body, status, headers=headers, direct_passthrough=request.method != "HEAD")

@bp.get("/capabilities")
def capabilities():
    result = capability_summary(); result.update({"transport": "http", "curl_resume": True, "resources": ["documents", "contacts", "calendars", "tasks"]}); return jsonify(result)
@bp.route("/documents/<document_id>/manifest", methods=["GET", "HEAD"])
def manifest(document_id: str):
    try: item, path = _document(document_id)
    except (ValueError, KeyError): return jsonify({"error": "not_found"}), 404
    digest = str(item.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest): digest = sha256_file(path)
    return jsonify({"document_id": document_id, "blob_hash": f"sha256:{digest}", "size": path.stat().st_size, "accept_ranges": "bytes", "download": f"/federation/v1/documents/{document_id}/blob", "content_addressed_download": f"/federation/v1/blobs/{digest}"})
@bp.route("/documents/<document_id>/blob", methods=["GET", "HEAD"])
def document_blob(document_id: str):
    try: item, path = _document(document_id)
    except (ValueError, KeyError): return jsonify({"error": "not_found"}), 404
    digest = str(item.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest): digest = sha256_file(path)
    return _send(path, digest)
@bp.route("/blobs/<digest>", methods=["GET", "HEAD"])
def blob(digest: str):
    try: normalized = normalize_sha256(digest); path = _blob_path(normalized)
    except ValueError: return jsonify({"error": "not_found"}), 404
    return _send(path, normalized)
@bp.get("/blobs/<digest>/manifest")
def blob_manifest(digest: str):
    try: path = _blob_path(digest)
    except ValueError: return jsonify({"error": "not_found"}), 404
    manifest = build_manifest(path, DEFAULT_CHUNK_SIZE)
    if manifest["blob_hash"] != normalize_sha256(digest): return jsonify({"error": "index_hash_mismatch"}), 409
    manifest["chunk_download_template"] = f"/federation/v1/blobs/{manifest['blob_hash']}/chunks/{{index}}"
    return jsonify(manifest)
@bp.route("/blobs/<digest>/chunks/<int:index>", methods=["GET", "HEAD"])
def blob_chunk(digest: str, index: int):
    try: normalized = normalize_sha256(digest); path = _blob_path(normalized); start, end = chunk_range(index, path.stat().st_size, DEFAULT_CHUNK_SIZE)
    except (ValueError, IndexError): return jsonify({"error": "not_found"}), 404
    if start < 0 or end < start or start >= path.stat().st_size: return jsonify({"error": "chunk_not_found"}), 404
    return _send(path, normalized, (start, end))
@bp.get("/blobs/<digest>/availability")
def availability(digest: str):
    try: path = _blob_path(digest); manifest = build_manifest(path, DEFAULT_CHUNK_SIZE)
    except ValueError: return jsonify({"error": "not_found"}), 404
    return jsonify({"blob_hash": manifest["blob_hash"], "chunk_count": manifest["chunk_count"], "available": [[0, manifest["chunk_count"] - 1]] if manifest["chunk_count"] else []})
