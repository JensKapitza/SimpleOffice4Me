"""Application-wide audit coverage for state-changing HTTP requests.

Domain stores keep their detailed RevisionHistory entries. This module adds a
privacy-preserving request layer so state changes have a common
"who / what / when / outcome" record even when a route forgot a specialized
audit event.
"""

from __future__ import annotations

from typing import Any

from flask import g, request

from .access_control import audit

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
STATE_CHANGING_GET_ENDPOINTS = {"auth.logout"}
FORM_MIMETYPES = {"application/x-www-form-urlencoded", "multipart/form-data"}
SENSITIVE_NAMES = {
    "password", "passwd", "secret", "token", "authorization", "cookie",
    "api_key", "apikey", "private_key", "access_token", "refresh_token",
}


def _safe_name(name: str) -> bool:
    folded = str(name).casefold().replace("-", "_")
    return not any(sensitive in folded for sensitive in SENSITIVE_NAMES)


def _safe_route_values(values: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (values or {}).items():
        if not _safe_name(key):
            continue
        result[str(key)[:80]] = str(value)[:200]
    return result


def _actor_for_response(status_code: int, endpoint: str):
    """Return only an authenticated or successfully established identity."""
    user = getattr(g, "user", None)
    if user is not None:
        return user
    authorization = request.authorization
    if status_code < 400 and authorization and authorization.username:
        return {"id": None, "username": authorization.username[:160]}
    # Successful local self-registration is the one mutation where the actor
    # does not have a session yet. Only trust the submitted name after the
    # endpoint accepted it and redirected to login.
    if endpoint == "auth.register" and 300 <= status_code < 400:
        username = request.form.get("username", "").strip()
        if username:
            return {"id": None, "username": username[:160]}
    return None


def audit_mutation_response(response):
    """Record one safe event for every attributable state-changing request."""
    method = request.method.upper()
    endpoint = (request.endpoint or "unknown")[:180]
    if method not in MUTATING_METHODS and endpoint not in STATE_CHANGING_GET_ENDPOINTS:
        return response
    actor = _actor_for_response(int(response.status_code), endpoint)
    if actor is None:
        return response

    blueprint = endpoint.split(".", 1)[0] if "." in endpoint else endpoint
    if response.status_code < 400:
        outcome = "success"
    elif response.status_code in {401, 403}:
        outcome = "denied"
    else:
        outcome = "failed"

    # Do not trigger form parsing for raw WebDAV/SFTP-style uploads after the
    # business handler has already consumed a potentially large request body.
    if request.mimetype in FORM_MIMETYPES:
        form_fields = sorted({str(key)[:80] for key in request.form.keys() if _safe_name(key)})[:100]
        file_fields = sorted({str(key)[:80] for key in request.files.keys() if _safe_name(key)})[:50]
    else:
        form_fields = []
        file_fields = []
    route_rule = str(request.url_rule.rule)[:300] if request.url_rule is not None else ""
    detail = {
        "method": method,
        "endpoint": endpoint,
        "route": route_rule,
        "status": int(response.status_code),
        "request_id": str(getattr(g, "request_id", ""))[:80],
        "route_values": _safe_route_values(request.view_args),
        "form_fields": form_fields,
        "file_fields": file_fields,
        "content_type": str(request.mimetype or "")[:120],
    }
    action = {
        "auth.logout": "logout",
        "auth.register": "account_registered",
    }.get(endpoint, "http_mutation")
    target_type = "session" if endpoint == "auth.logout" else ("user" if endpoint == "auth.register" else f"endpoint:{blueprint}")
    target_id = actor["username"] if endpoint == "auth.register" else endpoint
    try:
        audit(action, target_type, target_id, outcome=outcome, detail=detail, actor=actor)
    except Exception:
        # Audit persistence must never convert an already committed business
        # transaction into a 500. The failure itself still goes to app logs.
        try:
            from flask import current_app
            current_app.logger.exception("Failed to persist mutation audit event")
        except Exception:
            pass
    return response
