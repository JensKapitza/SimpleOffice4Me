"""HTTP access to content-defined SHA-512 blocks used by SOFP deduplication."""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, Response, current_app, jsonify, request

from .document_store import DocumentStore
from .federation_blocks import FederationBlockStore, content_manifest_valid, normalize_sha512, sha512_bytes
from .federation_core import normalize_sha256


bp = Blueprint("federation_blocks_http", __name__, url_prefix="/federation/v1/blocks")
MAX_BLOCK_RESPONSE = 64 * 1024 * 1024


def _authorized() -> bool:
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not expected:
        return bool(current_app.testing)
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(expected, supplied)


@bp.before_request
def authenticate_blocks():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _blob_path(digest: str):
    digest = normalize_sha256(digest)
    documents = DocumentStore(_root())
    documents.initialize()
    with documents._db() as db:
        row = db.execute(
            "SELECT relative_path FROM scan_file WHERE sha256=? ORDER BY relative_path LIMIT 1",
            (digest,),
        ).fetchone()
    if row is None:
        raise ValueError("blob unavailable")
    unresolved = documents.root / str(row["relative_path"])
    if unresolved.is_symlink():
        raise ValueError("blob unavailable")
    path = unresolved.resolve()
    if documents.root not in (path, *path.parents) or not path.is_file():
        raise ValueError("blob unavailable")
    return path


@bp.get("/blobs/<digest>/manifest")
def content_manifest(digest: str):
    try:
        path = _blob_path(digest)
        manifest = FederationBlockStore(_root()).manifest_for_file(path)
        if not content_manifest_valid(manifest):
            raise ValueError("invalid local manifest")
    except (OSError, ValueError):
        return jsonify({"error": "not_found"}), 404
    result = dict(manifest)
    result["block_download_template"] = "/federation/v1/blocks/{sha512}"
    return jsonify(result)


@bp.post("/availability")
def block_availability():
    body = request.get_json(silent=True) or {}
    values = body.get("sha512") or []
    if not isinstance(values, list) or len(values) > 5000:
        return jsonify({"error": "invalid_hash_list"}), 400
    store = FederationBlockStore(_root())
    available = store.available(values)
    return jsonify({"hash_algorithm": "sha512", "available": sorted(available)})


@bp.get("/<digest>")
def block(digest: str):
    try:
        normalized = normalize_sha512(digest)
        data = FederationBlockStore(_root()).read_block(normalized)
        if len(data) > MAX_BLOCK_RESPONSE or sha512_bytes(data) != normalized:
            raise ValueError("invalid local block")
    except (KeyError, OSError, ValueError):
        return jsonify({"error": "block_not_found"}), 404
    return Response(
        data,
        200,
        {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
            "X-Content-SHA512": normalized,
            "Cache-Control": "private, immutable",
        },
    )
