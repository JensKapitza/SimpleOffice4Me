"""SOFP document catalog exchange for offline planning and download requests."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from .document_origin import document_origin_tags
from .document_store import DocumentStore, sha256_file

bp = Blueprint("federation_catalog_http", __name__, url_prefix="/federation/v1/catalog")
MAX_PAGE_SIZE = 1000


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _authorized() -> bool:
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
    rows = []
    for item in store.list_documents():
        path = (store.root / str(item.get("last_path", ""))).resolve()
        if store.root not in (path, *path.parents) or not path.is_file() or path.is_symlink():
            continue
        digest = str(item.get("sha256") or "").casefold()
        if len(digest) != 64:
            digest = sha256_file(path)
        rows.append({
            "document_id": str(item.get("document_id", "")),
            "blob_hash": digest,
            "path": str(item.get("last_path", "")),
            "size": path.stat().st_size,
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
    rows = _catalog_rows()
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
