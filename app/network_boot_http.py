"""HTTP boot delivery and authenticated federation sharing for network boot."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, send_file

from .federation_http import _authorized
from .federation_store import FederationStore
from .network_boot import (
    federation_manifest,
    render_ipxe,
    safe_asset_path,
    save_boot_settings,
    store_asset,
)

bp = Blueprint("network_boot_http", __name__, url_prefix="/network-boot")
federation_bp = Blueprint("federation_network_boot_http", __name__, url_prefix="/federation/v1/network-boot")
MAX_FEDERATED_ASSET = 16 * 1024 * 1024 * 1024
POLICY_OFFERS = "offers_network_boot"
POLICY_STORES = "stores_network_boot"


def _config_path() -> Path:
    configured = os.environ.get("SIMPLEOFFICE_MINI_SERVICES_CONFIG", "").strip()
    return Path(configured).expanduser() if configured else Path(current_app.instance_path) / "mini-services.json"


@bp.get("/ipxe")
def ipxe_script():
    profile = request.args.get("profile", "").strip()[:80]
    try:
        text = render_ipxe(profile, _config_path(), request_base=request.host_url.rstrip("/"))
    except ValueError as exc:
        return Response(str(exc) + "\n", 404, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"})
    return Response(text, 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"})


@bp.route("/files/<path:relative>", methods=["GET", "HEAD"])
def boot_file(relative: str):
    try:
        path = safe_asset_path(relative, _config_path())
    except ValueError:
        return Response("not found\n", 404, {"Content-Type": "text/plain; charset=utf-8"})
    response = send_file(path, conditional=True, etag=True, max_age=300)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@federation_bp.before_request
def federation_authenticate():
    if not _authorized():
        return Response(
            "federation authentication required\n", 401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Network Boot Federation"', "Cache-Control": "no-store"},
        )
    return None


def _peer_allowed(peer_id: str, capability: str) -> bool:
    if current_app.testing and not peer_id:
        return True
    peer = FederationStore(current_app.config["DOCUMENT_ROOT"]).get_peer(peer_id) if peer_id else None
    policy = peer.get("policy") if peer else {}
    section = policy.get("network_boot", {}) if isinstance(policy, dict) else {}
    return bool(peer and peer.get("enabled") and isinstance(section, dict) and section.get(capability) is True)


def _requesting_peer() -> str:
    return request.headers.get("X-SimpleOffice-Peer-ID", "").strip()[:128]


# Offering endpoints: the local server is the source. A remote peer may read
# only when that peer is configured locally with "offers_network_boot" from
# the peer's perspective, i.e. it is allowed to consume our offered boot data.
@federation_bp.get("/manifest")
def network_boot_manifest():
    peer_id = _requesting_peer()
    if peer_id and not _peer_allowed(peer_id, POLICY_OFFERS):
        return jsonify({"error": "peer_may_not_use_network_boot_offer"}), 403
    response = jsonify(federation_manifest(_config_path()))
    response.headers["Cache-Control"] = "no-store"
    return response


@federation_bp.route("/assets/<path:relative>", methods=["GET", "HEAD"])
def network_boot_asset(relative: str):
    peer_id = _requesting_peer()
    if peer_id and not _peer_allowed(peer_id, POLICY_OFFERS):
        return jsonify({"error": "peer_may_not_use_network_boot_offer"}), 403
    try:
        path = safe_asset_path(relative, _config_path())
    except ValueError:
        return jsonify({"error": "not_found"}), 404
    return send_file(path, conditional=True, etag=True, max_age=0)


# Storage endpoints: the local server is a mirror/backup target. Incoming data
# is accepted only from peers explicitly configured as "stores_network_boot".
@federation_bp.put("/storage/assets/<path:relative>")
def store_network_boot_asset(relative: str):
    peer_id = _requesting_peer()
    if not _peer_allowed(peer_id, POLICY_STORES):
        return jsonify({"error": "peer_may_not_store_network_boot_here"}), 403
    expected = request.headers.get("X-Content-SHA256", "").strip().casefold()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        return jsonify({"error": "sha256_required"}), 400
    digest = hashlib.sha256(); total = 0
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as temporary:
        while True:
            block = request.stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_FEDERATED_ASSET:
                return jsonify({"error": "asset_too_large"}), 413
            digest.update(block); temporary.write(block)
        if digest.hexdigest() != expected:
            return jsonify({"error": "sha256_mismatch"}), 400
        temporary.seek(0)
        result = store_asset(temporary, relative, _config_path(), max_bytes=MAX_FEDERATED_ASSET)
    FederationStore(current_app.config["DOCUMENT_ROOT"]).record_event(
        "network_boot_asset_stored_for_peer", peer_id=peer_id, detail=result
    )
    return jsonify(result), 201


@federation_bp.put("/storage/settings")
def store_network_boot_settings():
    peer_id = _requesting_peer()
    if not _peer_allowed(peer_id, POLICY_STORES):
        return jsonify({"error": "peer_may_not_store_network_boot_here"}), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid_settings"}), 400
    try:
        # The federation sender supplies portable settings only. Local service
        # enable/bind/port values remain unchanged by save/merge callers.
        current = __import__("app.network_boot", fromlist=["load_boot_settings"]).load_boot_settings(_config_path())
        merged = dict(current)
        for key in ("default_profile", "bios_loader", "uefi_x64_loader", "uefi_arm64_loader", "profiles"):
            if key in body:
                merged[key] = body[key]
        clean = save_boot_settings(merged, _config_path())
    except ValueError as exc:
        return jsonify({"error": "invalid_settings", "detail": str(exc)}), 400
    FederationStore(current_app.config["DOCUMENT_ROOT"]).record_event(
        "network_boot_settings_stored_for_peer", peer_id=peer_id, detail={"profiles": len(clean["profiles"])}
    )
    return jsonify({"stored": True, "profiles": len(clean["profiles"])})
