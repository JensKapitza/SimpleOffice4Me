"""Privacy-scoped V2 federation block transport."""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, Response, current_app, jsonify, request

from .document_store import DocumentStore
from .federation_blocks import FederationBlockStore, content_manifest_valid, sha512_bytes
from .federation_core import normalize_sha256
from .safe_paths import resolve_under
from .v2.scoped_dedup import (
    SCHEMA,
    create_dedup_session,
    scoped_block_token,
    validate_dedup_session,
)


bp = Blueprint("federation_blocks_v2_http", __name__, url_prefix="/federation/v2/blocks")
MAX_BLOCK_RESPONSE = 64 * 1024 * 1024


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


def _local_manifest(blob_hash: str):
    path = _blob_path(blob_hash)
    manifest = FederationBlockStore(_root()).manifest_for_file(path)
    if not content_manifest_valid(manifest):
        raise ValueError("invalid local content manifest")
    return manifest


@bp.get("/capabilities")
def scoped_capabilities():
    return jsonify({
        "schema": SCHEMA,
        "session_scoped_equality": True,
        "stable_block_hashes_exposed": False,
        "availability_oracle": False,
        "cross_file_reuse": True,
        "max_block_response": MAX_BLOCK_RESPONSE,
    })


@bp.get("/blobs/<digest>/manifest")
def scoped_manifest(digest: str):
    try:
        blob_hash = normalize_sha256(digest)
        source = _local_manifest(blob_hash)
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
    except (OSError, ValueError):
        return jsonify({"error": "not_found"}), 404
    return jsonify({
        "schema": SCHEMA,
        "session": session,
        "expires_at": expires_at,
        "size": int(source["size"]),
        "block_count": len(blocks),
        "blocks": blocks,
    })


@bp.get("/blobs/<digest>/blocks/<int:index>")
def scoped_block(digest: str, index: int):
    session = str(request.args.get("session") or "")
    proof = str(request.args.get("proof") or "").casefold()
    try:
        blob_hash = normalize_sha256(digest)
        secret = _configured_secret()
        validate_dedup_session(secret, blob_hash, session)
        manifest = _local_manifest(blob_hash)
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
