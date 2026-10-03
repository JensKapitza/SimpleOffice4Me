"""Peer-bound moderation APIs; bearer tokens cannot impersonate reporters."""
import json

from flask import Blueprint, current_app, g, jsonify, request

from .federation_moderation import FederationModerationStore
from .federation_peer_auth import authenticate

bp = Blueprint("federation_moderation_http", __name__, url_prefix="/federation/v1/moderation")
MAX_REPORT_BYTES = 4096


@bp.before_request
def authenticate_moderation():
    # Read bounded bytes before HMAC authentication caches the request body,
    # including GET bodies from malformed or hostile clients.
    raw = request.stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        return jsonify({"error": "report_too_large"}), 413
    request._cached_data = raw
    try:
        g.moderation_reporter = authenticate(current_app.config["DOCUMENT_ROOT"], request)
    except (TypeError, ValueError):
        return jsonify({"error": "peer_authentication_required"}), 401
    return None


@bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/blacklist")
def blacklist():
    return jsonify(FederationModerationStore(current_app.config["DOCUMENT_ROOT"]).snapshot())


@bp.post("/reports")
def report():
    try:
        data = json.loads(request.get_data())
        if not isinstance(data, dict) or set(data) != {"peer_id", "reason"}:
            raise ValueError("invalid report")
        report_id = FederationModerationStore(current_app.config["DOCUMENT_ROOT"]).report(
            g.moderation_reporter, data["peer_id"], data["reason"],
        )
        return jsonify({"report_id": report_id, "status": "received"}), 201
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_report"}), 400
