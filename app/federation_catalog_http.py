"""SOFP document catalog exchange for offline planning and download requests."""
from __future__ import annotations

import hashlib
import hmac
import json
import os

from flask import Blueprint, Response, current_app, jsonify, request

from .document_origin import document_origin_tags
from .document_store import DocumentStore
from .federation_core import normalize_sha256
from .federation_peer_auth import authenticate as authenticate_peer
from .v2.contracts import ErrorCode, LogicalObjectId
from .v2.storage_runtime import storage_for

bp = Blueprint("federation_catalog_http", __name__, url_prefix="/federation/v1/catalog")
MAX_PAGE_SIZE = 1000


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _authorized() -> bool:
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        authenticate_peer(root, request)
        return True
    except (TypeError, ValueError):
        pass
    from .federation_moderation_auth import legacy_peer_allowed
    if not legacy_peer_allowed(root, request):
        return False
    # Compatibility only. This bearer authenticates access to the legacy
    # catalog, but it must never be treated as proof of a particular peer.
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not expected:
        return bool(current_app.testing)
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(expected, supplied)


@bp.before_request
def authenticate_catalog():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _bounded_int(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _catalog_rows() -> list[dict]:
    store = _store()
    storage = storage_for(store.root, "federation-transfer")
    rows = []
    for item in store.list_documents():
        if item.get("system_state") == "webdav_deleted" or item.get("deleted_at"):
            continue
        result = storage.stat(LogicalObjectId(str(item.get("document_id") or "")))
        if not result.ok:
            if result.error.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.INVALID_INPUT}:
                continue
            raise RuntimeError("catalog storage metadata unavailable")
        stored = result.value
        digest = normalize_sha256(stored.version)
        rows.append({
            "document_id": stored.object_id.value,
            "blob_hash": digest,
            "path": stored.location.relative_path,
            "size": stored.size,
            "modified_at": str(item.get("last_seen_at") or ""),
            "state": str(item.get("state") or "new")[:120],
            "tags": sorted({str(tag) for tag in item.get("tags", []) if str(tag).strip()}, key=str.casefold),
            "origin_tags": document_origin_tags(item),
        })
    rows.sort(key=lambda row: (row["path"].casefold(), row["document_id"]))
    return rows


def _generation(rows: list[dict]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(
            [row["document_id"], row["blob_hash"], row["path"], row["size"], row["modified_at"], row["tags"], row["origin_tags"]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


@bp.get("/documents")
def document_index():
    try:
        rows = _catalog_rows()
    except (OSError, RuntimeError, ValueError):
        return jsonify({"error": "catalog_unavailable"}), 503
    generation = _generation(rows)
    cursor = _bounded_int(request.args.get("cursor", "0"), 0, 0, max(0, len(rows)))
    limit = _bounded_int(request.args.get("limit", "250"), 250, 1, MAX_PAGE_SIZE)
    page = rows[cursor:cursor + limit]
    next_cursor = cursor + len(page)
    return jsonify({
        "schema": "sofp-document-index/v1",
        "generation": generation,
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < len(rows) else None,
        "count": len(page),
        "total": len(rows),
        "documents": page,
    })
