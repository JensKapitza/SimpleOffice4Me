"""HTTP access to content-defined SHA-512 blocks used by SOFP deduplication."""
from __future__ import annotations

import hashlib
import hmac

from flask import Blueprint, Response, current_app, jsonify, request

from .document_store import DocumentStore
from .federation_blocks import (
    DEFAULT_AVG_BLOCK,
    DEFAULT_MAX_BLOCK,
    DEFAULT_MIN_BLOCK,
    HASH_ALGORITHM,
    ROLLING_WINDOW,
    SCHEMA,
    FederationBlockStore,
    content_manifest_valid,
    normalize_sha512,
    sha512_bytes,
)
from .federation_core import normalize_sha256
from .federation_store import FederationStore
from . import federation_local_identity as _identity
from .safe_paths import resolve_under


bp = Blueprint("federation_blocks_http", __name__, url_prefix="/federation/v1/blocks")
MAX_BLOCK_RESPONSE = 64 * 1024 * 1024


def _authorized() -> bool:
    from .federation_moderation_auth import legacy_peer_allowed
    if not legacy_peer_allowed(current_app.config["DOCUMENT_ROOT"], request):
        return False
    expected = _identity.shared_token()
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.startswith("Bearer ") else ""
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return True
    if request.endpoint == "federation_blocks_http.block_roundtrip_test":
        try:
            from .federation_peer_auth import authenticate
            root = current_app.config["DOCUMENT_ROOT"]
            peer_id = authenticate(root, request)
            return peer_id not in FederationStore(root).banned_peer_ids()
        except (TypeError, ValueError):
            return False
    return bool(current_app.testing and not expected)


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
    try:
        path = resolve_under(documents.root, str(row["relative_path"]), strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError("blob unavailable") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError("blob unavailable")
    return path


@bp.get("/capabilities")
def block_capabilities():
    return jsonify({
        "schema": SCHEMA,
        "hash_algorithm": HASH_ALGORITHM,
        "content_defined": True,
        "rolling_window": ROLLING_WINDOW,
        "min_block_size": DEFAULT_MIN_BLOCK,
        "avg_block_size": DEFAULT_AVG_BLOCK,
        "max_block_size": DEFAULT_MAX_BLOCK,
        "cross_file_reuse": True,
        "local_first": True,
    })


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
    return jsonify({"hash_algorithm": HASH_ALGORITHM, "available": sorted(available)})


@bp.post("/test")
def block_roundtrip_test():
    """Echo a small payload back as SHA-256 (connectivity test, no storage)."""
    payload = request.get_data(cache=False, as_text=False) or b""
    if len(payload) > 4096:
        return jsonify({"error": "test_payload_too_large", "max_bytes": 4096}), 413
    response = jsonify({"ok": True, "sha256": hashlib.sha256(payload).hexdigest(),
                        "size": len(payload)})
    response.headers["Cache-Control"] = "no-store"
    return response


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
