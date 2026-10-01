"""HTTP facade for the additive SimpleOffice 3.0 federation contract."""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, Response, current_app, jsonify, request

from .db import get_db
from .mail_case_federation import MailCaseFederation
from .mail_client import MailStore
from .v3_capabilities import enabled
from .v3_federation import FederationContract, FederationEnvelope, capability_descriptor


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


def _mail_store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


def _active_local_user(username: str) -> bool:
    row = get_db().execute(
        "SELECT 1 FROM user WHERE username=? COLLATE NOCASE AND is_disabled=0",
        (str(username),),
    ).fetchone()
    return row is not None


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
        envelope = FederationEnvelope.from_mapping(payload)
        contract = FederationContract(
            current_app.config["DOCUMENT_ROOT"],
            _local_peer_id(),
        )
        result = contract.receive(payload)
    except (TypeError, ValueError):
        return jsonify({"status": "rejected", "error": "invalid_envelope"}), 400

    status = result.get("status")
    if envelope.object_type == "mail_cases" and status in {"accepted", "duplicate"}:
        try:
            mail_store = _mail_store()
            application = MailCaseFederation(
                current_app.config["DOCUMENT_ROOT"],
                _local_peer_id(),
                history=mail_store.history,
            ).apply(envelope, active_user=_active_local_user)
            result["application"] = application
        except (KeyError, PermissionError, ValueError):
            current_app.logger.warning(
                "Federated mail-case application rejected for message %s",
                envelope.message_id,
            )
            return jsonify({
                "status": "application_error",
                "error": "mail_case_application_failed",
                "message_id": envelope.message_id,
            }), 409

    if status in {"accepted", "duplicate"}:
        code = 200
    elif status in {"pending", "quarantined"}:
        code = 202
    else:
        code = 409
    return jsonify(result), code


@bp.route("/mail-cases/content/<token>", methods=["GET", "HEAD"])
def mail_case_content(token: str):
    try:
        mail_store = _mail_store()
        raw, digest = MailCaseFederation(
            current_app.config["DOCUMENT_ROOT"],
            _local_peer_id(),
            history=mail_store.history,
        ).content_for_grant(token, mail_store)
    except PermissionError:
        return jsonify({"error": "mail_case_content_denied"}), 403
    except (FileNotFoundError, KeyError, ValueError):
        return jsonify({"error": "mail_case_content_not_found"}), 404
    headers = {
        "Cache-Control": "private, no-store, no-transform",
        "Content-Type": "message/rfc822",
        "Content-Length": str(len(raw)),
        "X-Content-SHA512": digest,
        "ETag": f'"sha512:{digest}"',
    }
    return Response(b"" if request.method == "HEAD" else raw, 200, headers)
