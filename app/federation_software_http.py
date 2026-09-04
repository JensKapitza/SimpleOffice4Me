"""Authenticated federation endpoints for offline SimpleOffice releases."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from .federation_core import build_manifest, chunk_range, normalize_sha256
from .federation_http import _authorized, _send
from .federation_store import FederationStore
from .software_distribution import SoftwareDistributionStore, local_release_info

bp = Blueprint("federation_software_http", __name__, url_prefix="/federation/v1/software")


def _distribution() -> SoftwareDistributionStore:
    return SoftwareDistributionStore(current_app.config["DOCUMENT_ROOT"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


@bp.before_request
def authenticate_software():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _public_release(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    return {
        "release": entry.get("release") or {},
        "bundle": {
            "sha256": entry.get("archive_sha256", ""),
            "size": int(entry.get("archive_size") or 0),
            "wheelhouse": entry.get("wheelhouse") or {},
        },
    }


@bp.get("/status")
def software_status():
    distribution = _distribution()
    return jsonify({
        "software_distribution": True,
        "local": local_release_info(),
        "latest": _public_release(distribution.latest()),
        "policy": "explicit-peer-policy",
        "update_mode": "git-fast-forward-only",
    })


@bp.get("/releases/current")
def current_release():
    distribution = _distribution()
    entry = distribution.latest()
    if not entry:
        return jsonify({"error": "release_not_built"}), 404
    path = distribution.release_path(entry["archive_sha256"])
    manifest = build_manifest(path)
    public = _public_release(entry) or {}
    public["manifest"] = manifest
    return jsonify(public)


@bp.get("/releases/<digest>/manifest")
def release_manifest(digest: str):
    try:
        path = _distribution().release_path(digest)
        return jsonify(build_manifest(path))
    except ValueError:
        return jsonify({"error": "release_not_found"}), 404


@bp.route("/releases/<digest>/blob", methods=["GET", "HEAD"])
def release_blob(digest: str):
    try:
        normalized = normalize_sha256(digest)
        path = _distribution().release_path(normalized)
    except ValueError:
        return jsonify({"error": "release_not_found"}), 404
    return _send(path, normalized)


@bp.route("/releases/<digest>/chunks/<int:index>", methods=["GET", "HEAD"])
def release_chunk(digest: str, index: int):
    try:
        normalized = normalize_sha256(digest)
        path = _distribution().release_path(normalized)
        manifest = build_manifest(path)
        start, end = chunk_range(index, path.stat().st_size, int(manifest["chunk_size"]))
    except (ValueError, IndexError):
        return jsonify({"error": "chunk_not_found"}), 404
    if start < 0 or end < start or start >= path.stat().st_size:
        return jsonify({"error": "chunk_not_found"}), 404
    return _send(path, normalized, (start, end))


@bp.post("/offers")
def receive_offer():
    body = request.get_json(silent=True) or {}
    source_peer = str(body.get("source_peer") or "").strip()[:128]
    peer = _federation().get_peer(source_peer) if source_peer else None
    if not peer or not peer.get("enabled"):
        return jsonify({"error": "unknown_source_peer"}), 403
    policy = peer.get("policy") or {}
    software = policy.get("software", {}) if isinstance(policy, dict) else {}
    if software.get("receive") is not True:
        return jsonify({"error": "software_receive_not_allowed"}), 403
    try:
        entry = _distribution().record_offer(source_peer, body)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_offer"}), 400
    _federation().record_event(
        "software_offer_received",
        peer_id=source_peer,
        detail={"release": entry.get("release", {}), "bundle": entry.get("bundle", {})},
    )
    return jsonify({"accepted": True, "status": entry["status"]}), 202
