"""Application-wide audit coverage for state-changing HTTP requests.

Domain stores keep their detailed RevisionHistory entries.  This module adds a
small, privacy-preserving request layer so every authenticated mutation has a
common "who / what / when / outcome" record even when a route forgot to emit a
specialized audit event.
"""

from __future__ import annotations

from typing import Any

from flask import g, request

from .access_control import audit

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
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
        text = str(value)
        result[str(key)[:80]] = text[:200]
    return result


def _actor_for_response(status_code: int):
    """Return only an authenticated/accepted identity.

    Session users are authoritative.  Basic-auth usernames are used only after
    a successful protocol request, so a rejected caller cannot inject an
    arbitrary actor name into the audit trail.
    """
    user = getattr(g, "user", None)
    if user is not None:
        return user
    authorization = request.authorization
    if status_code < 400 and authorization and authorization.username:
        return {"id": None, "username": authorization.username[:160]}
    return None


def audit_mutation_response(response):
    """Record one safe event for every authenticated state-changing request."""
    method = request.method.upper()
    if method not in MUTATING_METHODS:
        return response
    actor = _actor_for_response(int(response.status_code))
    if actor is None:
        return response

    endpoint = (request.endpoint or "unknown")[:180]
    blueprint = endpoint.split(".", 1)[0] if "." in endpoint else endpoint
    if response.status_code < 400:
        outcome = "success"
    elif response.status_code in {401, 403}:
        outcome = "denied"
    else:
        outcome = "failed"

    form_fields = sorted({str(key)[:80] for key in request.form.keys() if _safe_name(key)})[:100]
    file_fields = sorted({str(key)[:80] for key in request.files.keys() if _safe_name(key)})[:50]
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
    try:
        audit(
            "http_mutation",
            f"endpoint:{blueprint}",
            endpoint,
            outcome=outcome,
            detail=detail,
            actor=actor,
        )
    except Exception:
        # Audit failures must be visible in the application log, but must not
        # turn an otherwise successful business transaction into a 500 after it
        # has already been committed.
        try:
            from flask import current_app
            current_app.logger.exception("Failed to persist mutation audit event")
        except Exception:
            pass
    return response
