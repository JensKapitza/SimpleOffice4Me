"""HTTP facade for the additive SimpleOffice 3.0 federation contract."""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, Response, current_app, jsonify, request

from .db import get_db
from .v3_capabilities import enabled
from .v3_federation import FederationContract, capability_descriptor


bp = Blueprint("v3_federation", __name__, url_prefix="/federation/v3")


def _bearer() -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


def _authorized() -> bool:
    expected = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    supplied = _bearer()
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return True
    return bool(current_app.testing and not expected)


def _local_peer_id() -> str:
    return (
        os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
        or "simpleoffice-local"
    )


def _active_local_user(username: str) -> bool:
    return get_db().execute(
        "SELECT 1 FROM user WHERE username=? COLLATE NOCASE AND is_disabled=0",
        (str(username),),
    ).fetchone() is not None


@bp.before_request
def require_contract():
    if not enabled("v3.federation"):
        return Response("not found\n", 404, {"Cache-Control": "no-store"})
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {
                "WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation V3"',
                "Cache-Control": "no-store",
            },
        )
    return None


@bp.get("/capabilities")
def capabilities():
    return jsonify(capability_descriptor())


@bp.post("/peers/<peer_id>/capabilities")
def remember_capabilities(peer_id: str):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_capabilities"}), 400
    try:
        contract = FederationContract(
            current_app.config["DOCUMENT_ROOT"],
            _local_peer_id(),
        )
        result = contract.store.remember_capabilities(peer_id, payload)
    except ValueError:
        return jsonify({"error": "invalid_capabilities"}), 400
    return jsonify(result)


@bp.post("/receive")
def receive():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "rejected", "error": "invalid_envelope"}), 400
    try:
        result = FederationContract(
            current_app.config["DOCUMENT_ROOT"],
            _local_peer_id(),
            mail_case_user_active=_active_local_user,
        ).receive(payload)
    except (TypeError, ValueError):
        return jsonify({"status": "rejected", "error": "invalid_envelope"}), 400
    status = result.get("status")
    if status in {"accepted", "duplicate"}:
        code = 200
    elif status in {"pending", "quarantined"}:
        code = 202
    else:
        code = 409
    return jsonify(result), code
