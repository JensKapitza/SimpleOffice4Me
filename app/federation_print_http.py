"""Authenticated federation printing with enforceable retention ceilings."""
from __future__ import annotations

import hashlib
import hmac
import json
import os

from flask import Blueprint, Response, current_app, jsonify, request

from .federation_http import _authorized
from .federation_print_auth import claim_nonce, identify_source_peer, parse_proof_headers
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

    try:
        proof_values, proof_signature = parse_proof_headers(request.headers, printer_id)
        source_peer, proof = identify_source_peer(_federation(), proof_values, proof_signature)
    except ValueError as exc:
        return jsonify({"error": "peer_identity_rejected", "detail": str(exc)}), 403

    claimed_peer = request.headers.get("X-SimpleOffice-Peer-ID", "").strip()[:128]
    if claimed_peer and not hmac.compare_digest(claimed_peer, source_peer):
        return jsonify({"error": "peer_identity_mismatch"}), 403

    expected_revision = proof.policy_revision
    revision = store.policy_revision()
    if not expected_revision:
        return jsonify({"error": "policy_revision_required", "policy_revision": revision}), 428
    if not hmac.compare_digest(expected_revision, revision):
        return jsonify({"error": "policy_changed", "policy_revision": revision}), 409

    raw_ceiling = proof.retention_ceiling
    if raw_ceiling not in RETENTION_ORDER:
        return jsonify({"error": "invalid_retention_ceiling"}), 400
    retention_ceiling = normalize_retention(raw_ceiling)
    ttl_ceiling = proof.ttl_ceiling_seconds
    if 0 < ttl_ceiling < 60:
        return jsonify({"error": "ttl_ceiling_below_minimum", "minimum_seconds": 60}), 400

    declared = request.content_length
    if proof.payload_size > settings["max_job_bytes"]:
        return jsonify({"error": "job_too_large", "max_job_bytes": settings["max_job_bytes"]}), 413
    if declared is not None and declared > settings["max_job_bytes"]:
        return jsonify({"error": "job_too_large", "max_job_bytes": settings["max_job_bytes"]}), 413

    payload = request.get_data(cache=False, as_text=False)
    if len(payload) > settings["max_job_bytes"]:
        return jsonify({"error": "job_too_large", "max_job_bytes": settings["max_job_bytes"]}), 413
    if len(payload) != proof.payload_size:
        return jsonify({"error": "signed_payload_size_mismatch"}), 400
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(payload_sha256, proof.payload_sha256):
        return jsonify({"error": "signed_payload_hash_mismatch"}), 400

    revision_after_upload = store.policy_revision()
    if not hmac.compare_digest(expected_revision, revision_after_upload):
        return jsonify({"error": "policy_changed", "policy_revision": revision_after_upload}), 409
    revision = revision_after_upload

    # Re-authenticate the peer after the complete upload. This re-reads the
    # current peer record and therefore catches disabled peers, revoked
    # printing.receive permissions and changed peer credentials before spooling.
    try:
        source_peer_after, proof_after = identify_source_peer(
            _federation(), proof_values, proof_signature
        )
    except ValueError as exc:
        return jsonify({"error": "peer_permission_changed", "detail": str(exc)}), 403
    if not hmac.compare_digest(source_peer_after, source_peer):
        return jsonify({"error": "peer_identity_changed"}), 403
    proof = proof_after

    if not claim_nonce(current_app.config["DOCUMENT_ROOT"], source_peer, proof.nonce):
        return jsonify({"error": "replayed_print_request"}), 409

    try:
        result = store.submit(
            printer_id,
            payload,
            content_type=proof.content_type,
            filename=proof.filename,
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
            "request_nonce": proof.nonce,
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
                "request_nonce": proof.nonce,
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
            detail={
                "printer_id": printer_id,
                "request_nonce": proof.nonce,
                "error_type": type(exc).__name__,
                "policy_revision": revision,
            },
        )
        return jsonify({"error": str(exc), "policy_revision": revision}), 400
