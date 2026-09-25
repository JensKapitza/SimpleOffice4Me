"""Privacy-scoped V2 federation block transport."""
from __future__ import annotations

import hmac
import os
import time

from flask import Blueprint, Response, current_app, g, jsonify, request

from .document_store import DocumentStore, sha256_file
from .federation_blocks import FederationBlockStore, content_manifest_valid, sha512_bytes
from .federation_core import normalize_sha256
from .federation_peer_auth import authenticate as authenticate_peer
from .federation_store import FederationStore
from .safe_paths import resolve_under
from .v2.scoped_dedup import (
    SCHEMA,
    create_dedup_session,
    scoped_block_token,
    validate_dedup_session,
)


bp = Blueprint("federation_blocks_v2_http", __name__, url_prefix="/federation/v2/blocks")
MAX_BLOCK_RESPONSE = 64 * 1024 * 1024
DEDUP_REQUEST_LIMIT = 120
DEDUP_REQUEST_WINDOW = 60


def _configured_secret() -> str:
    return os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()


def _supplied_secret() -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


@bp.before_request
def authenticate_scoped_blocks():
    expected = _configured_secret()
    if not expected:
        return jsonify({"error": "scoped_dedup_not_configured"}), 503
    supplied = _supplied_secret()
    if not supplied or not hmac.compare_digest(expected, supplied):
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    if request.endpoint == "federation_blocks_v2_http.scoped_capabilities":
        return None
    try:
        peer_id = authenticate_peer(_root(), request)
    except ValueError:
        return jsonify({"error": "peer_authentication_failed"}), 401
    federation = FederationStore(_root())
    now = int(time.time())
    if federation.recent_event_count(
        "scoped_dedup_request",
        peer_id=peer_id,
        since=now - DEDUP_REQUEST_WINDOW,
    ) >= DEDUP_REQUEST_LIMIT:
        federation.record_event(
            "scoped_dedup_rate_limited",
            peer_id=peer_id,
            detail={"window_seconds": DEDUP_REQUEST_WINDOW},
        )
        return jsonify({"error": "scoped_dedup_rate_limited"}), 429
    federation.record_event(
        "scoped_dedup_request",
        peer_id=peer_id,
        detail={"endpoint": str(request.endpoint or "")[:120]},
    )
    g.scoped_dedup_peer_id = peer_id
    return None


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _document_source(document_id: str):
    documents = DocumentStore(_root())
    item = documents.get_document(str(document_id or ""))
    if item.get("system_state") == "webdav_deleted" or item.get("deleted_at"):
        raise ValueError("document unavailable")
    try:
        path = resolve_under(documents.root, str(item.get("last_path") or ""), strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError("document unavailable") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError("document unavailable")
    digest = normalize_sha256(str(item.get("sha256") or ""))
    return item, path, digest


def _local_manifest(document_id: str):
    _item, path, digest = _document_source(document_id)
    if sha256_file(path) != digest:
        raise ValueError("document content differs from indexed document")
    manifest = FederationBlockStore(_root()).manifest_for_file(path)
    if not content_manifest_valid(manifest):
        raise ValueError("invalid local content manifest")
    return digest, manifest


@bp.get("/capabilities")
def scoped_capabilities():
    return jsonify({
        "schema": SCHEMA,
        "session_scoped_equality": True,
        "stable_block_hashes_exposed": False,
        "availability_oracle": False,
        "peer_signed_requests": True,
        "document_scoped_lookup": True,
        "stable_blob_hash_in_path": False,
        "cross_file_reuse": True,
        "max_block_response": MAX_BLOCK_RESPONSE,
    })


@bp.get("/documents/<document_id>/manifest")
def scoped_manifest(document_id: str):
    try:
        blob_hash, source = _local_manifest(document_id)
        secret = _configured_secret()
        session = create_dedup_session(secret, blob_hash)
        expires_at = validate_dedup_session(secret, blob_hash, session)
        blocks = [
            {
                "index": int(block["index"]),
                "offset": int(block["offset"]),
                "length": int(block["length"]),
                "token": scoped_block_token(secret, session, str(block["sha512"])),
            }
            for block in source["blocks"]
        ]
    except (KeyError, OSError, ValueError):
        return jsonify({"error": "not_found"}), 404
    FederationStore(_root()).record_event(
        "scoped_dedup_manifest_issued",
        peer_id=str(getattr(g, "scoped_dedup_peer_id", "")),
        detail={"document_id": str(document_id)[:200], "block_count": len(blocks)},
    )
    return jsonify({
        "schema": SCHEMA,
        "document_id": str(document_id),
        "session": session,
        "expires_at": expires_at,
        "size": int(source["size"]),
        "block_count": len(blocks),
        "blocks": blocks,
    })


@bp.get("/documents/<document_id>/blocks/<int:index>")
def scoped_block(document_id: str, index: int):
    session = str(request.args.get("session") or "")
    proof = str(request.args.get("proof") or "").casefold()
    try:
        blob_hash, manifest = _local_manifest(document_id)
        secret = _configured_secret()
        validate_dedup_session(secret, blob_hash, session)
        if index < 0 or index >= len(manifest["blocks"]):
            raise ValueError("invalid block index")
        block = manifest["blocks"][index]
        expected = scoped_block_token(secret, session, str(block["sha512"]))
        if not hmac.compare_digest(expected, proof):
            raise ValueError("invalid scoped block proof")
        data = FederationBlockStore(_root()).read_block(str(block["sha512"]), int(block["length"]))
        if len(data) > MAX_BLOCK_RESPONSE or sha512_bytes(data) != str(block["sha512"]):
            raise ValueError("invalid local block")
    except (KeyError, OSError, ValueError):
        return jsonify({"error": "block_not_found"}), 404
    FederationStore(_root()).record_event(
        "scoped_dedup_block_read",
        peer_id=str(getattr(g, "scoped_dedup_peer_id", "")),
        detail={"document_id": str(document_id)[:200], "block_index": int(index)},
    )
    return Response(
        data,
        200,
        {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
            "X-SimpleOffice-Scoped-Block": proof,
            "Cache-Control": "no-store",
        },
    )


@bp.get("/blobs/<digest>/manifest")
@bp.get("/blobs/<digest>/blocks/<int:index>")
def retired_hash_scoped_endpoint(digest: str, index: int | None = None):
    return jsonify({"error": "document_scoped_endpoint_required"}), 404
