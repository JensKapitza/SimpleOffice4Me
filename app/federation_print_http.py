"""Authenticated federation printing with enforceable retention ceilings."""
from __future__ import annotations

import hashlib
import hmac
import json
import os

from flask import Blueprint, Response, current_app, jsonify, request

from .federation_http import _authorized
from .federation_store import FederationStore
from .printershare_store import PrinterShareStore, RETENTION_ORDER, normalize_retention


bp = Blueprint("federation_print_http", __name__, url_prefix="/federation/v1/print")


def _store() -> PrinterShareStore:
    return PrinterShareStore(current_app.config["DOCUMENT_ROOT"], current_app.config["SECRET_KEY"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


@bp.before_request
def authenticate():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation Print"', "Cache-Control": "no-store"},
        )
    return None


def _source_allowed(source_peer: str) -> bool:
    if not source_peer:
        return True
    peer = _federation().get_peer(source_peer)
    if not peer:
        return True
    policy = peer.get("policy") or {}
    printing = policy.get("printing", {}) if isinstance(policy, dict) else {}
    return printing.get("receive") is not False


def _receipt_hmac(receipt: dict) -> str:
    token = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not token and current_app.testing:
        token = str(current_app.config.get("SECRET_KEY", ""))
    if not token:
        raise RuntimeError("Federation-Token fehlt; Druckbestätigung kann nicht authentisiert werden")
    payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(token.encode("utf-8"), payload, hashlib.sha256).hexdigest()


@bp.get("/capabilities")
def capabilities():
    response = jsonify(_store().federation_capabilities())
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/jobs/<printer_id>")
def submit_job(printer_id: str):
    store = _store()
    settings = store.settings()
    if not settings["enabled"] or not settings["federation_enabled"]:
        return jsonify({"error": "printing_disabled"}), 403
    source_peer = request.headers.get("X-SimpleOffice-Peer-ID", "").strip()[:128]
    if not _source_allowed(source_peer):
        return jsonify({"error": "peer_policy_rejects_printing"}), 403

    revision = store.policy_revision()
    expected_revision = request.headers.get("X-SimpleOffice-Policy-Revision", "").strip()
    if not expected_revision:
        return jsonify({"error": "policy_revision_required", "policy_revision": revision}), 428
    if not hmac.compare_digest(expected_revision, revision):
        return jsonify({"error": "policy_changed", "policy_revision": revision}), 409

    raw_ceiling = request.headers.get("X-SimpleOffice-Retention-Ceiling", "no_store").strip().casefold()
    if raw_ceiling not in RETENTION_ORDER:
        return jsonify({"error": "invalid_retention_ceiling"}), 400
    retention_ceiling = normalize_retention(raw_ceiling)
    try:
        ttl_ceiling = max(0, int(request.headers.get("X-SimpleOffice-TTL-Ceiling", "0") or 0))
    except ValueError:
        return jsonify({"error": "invalid_ttl_ceiling"}), 400

    declared = request.content_length
    if declared is not None and declared > settings["max_job_bytes"]:
        return jsonify({"error": "job_too_large", "max_job_bytes": settings["max_job_bytes"]}), 413
    payload = request.get_data(cache=False, as_text=False)
    if len(payload) > settings["max_job_bytes"]:
        return jsonify({"error": "job_too_large", "max_job_bytes": settings["max_job_bytes"]}), 413
    content_type = request.headers.get("X-SimpleOffice-Content-Type", "application/octet-stream").split(";", 1)[0].strip()[:200]
    filename = request.headers.get("X-SimpleOffice-Filename", "").strip()[:240]

    try:
        result = store.submit(
            printer_id,
            payload,
            content_type=content_type,
            filename=filename,
            source="federation",
            source_peer=source_peer,
            retention_ceiling=retention_ceiling,
            ttl_ceiling_seconds=ttl_ceiling,
            policy_revision=revision,
            federation=True,
        )
        receipt = {
            "schema": 1,
            "job_id": result["job_id"],
            "printer_id": result["printer_id"],
            "status": result["status"],
            "retention": result["retention"],
            "expires_at": result["expires_at"],
            "payload_sha256": result["payload_sha256"],
            "payload_size": result["payload_size"],
            "policy_revision": revision,
            "completed_at": result["completed_at"],
            "application_archive": result["application_archive"],
            "os_spooler_may_cache": True,
        }
        _federation().record_event(
            "print_job_spooled",
            peer_id=source_peer,
            detail={
                "job_id": result["job_id"],
                "printer_id": printer_id,
                "retention": result["retention"],
                "payload_sha256": result["payload_sha256"],
                "payload_size": result["payload_size"],
                "policy_revision": revision,
            },
        )
        response = jsonify({"receipt": receipt, "receipt_hmac_sha256": _receipt_hmac(receipt)})
        response.status_code = 201
        response.headers["Cache-Control"] = "no-store"
        return response
    except (OSError, RuntimeError, ValueError) as exc:
        _federation().record_event(
            "print_job_failed",
            peer_id=source_peer,
            detail={"printer_id": printer_id, "error_type": type(exc).__name__, "policy_revision": revision},
        )
        return jsonify({"error": str(exc), "policy_revision": revision}), 400
