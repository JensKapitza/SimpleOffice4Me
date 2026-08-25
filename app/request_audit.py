"""Application-wide audit coverage for state-changing HTTP requests."""

from __future__ import annotations

from typing import Any

from flask import g, request

from .access_control import audit
from .system_identity import system_info

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
STATE_CHANGING_GET_ENDPOINTS = {"auth.logout"}
FORM_MIMETYPES = {"application/x-www-form-urlencoded", "multipart/form-data"}
SENSITIVE_NAMES = {
    "password", "passwd", "secret", "token", "authorization", "cookie",
    "api_key", "apikey", "private_key", "access_token", "refresh_token",
}
TARGET_KEYS = (
    "document_id", "project_id", "contact_id", "event_id", "calendar_id",
    "share_id", "user_id", "account_id", "task_id", "object_id", "record_id",
    "error_id", "target_id", "id",
)


def _safe_name(name: str) -> bool:
    folded = str(name).casefold().replace("-", "_")
    return not any(sensitive in folded for sensitive in SENSITIVE_NAMES)


def _safe_route_values(values: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (values or {}).items():
        if _safe_name(key):
            result[str(key)[:80]] = str(value)[:200]
    return result


def _actor_for_response(status_code: int, endpoint: str):
    user = getattr(g, "user", None)
    if user is not None:
        return user
    authorization = request.authorization
    if status_code < 400 and authorization and authorization.username:
        return {"id": None, "username": authorization.username[:160]}
    if endpoint == "auth.register" and 300 <= status_code < 400:
        username = request.form.get("username", "").strip()
        if username:
            return {"id": None, "username": username[:160]}
    return None


def _semantic_action(endpoint: str, method: str) -> str:
    """Map technical endpoints to stable, human-searchable audit actions."""
    leaf = endpoint.split(".", 1)[-1].casefold()
    if endpoint == "auth.logout":
        return "logout"
    if endpoint == "auth.register":
        return "account_registered"
    if "restore" in leaf or "recover" in leaf:
        return "object_restored"
    if "share" in leaf or "grant" in leaf or "permission" in leaf or "access" in leaf:
        return "sharing_or_access_changed"
    if "sync" in leaf or "replication" in leaf or "google" in leaf:
        return "external_sync_requested"
    if "import" in leaf:
        return "import_requested"
    if "export" in leaf:
        return "export_requested"
    if "delete" in leaf or "remove" in leaf or method == "DELETE":
        return "object_deleted"
    if "archive" in leaf:
        return "archive_changed"
    if "create" in leaf or "new" in leaf:
        return "object_created"
    if "update" in leaf or "edit" in leaf or "save" in leaf or method in {"PUT", "PATCH"}:
        return "object_updated"
    return "http_mutation"


def _target(endpoint: str, route_values: dict[str, str], actor: dict[str, Any]) -> tuple[str, str]:
    if endpoint == "auth.logout":
        return "session", endpoint
    if endpoint == "auth.register":
        return "user", str(actor["username"])
    for key in TARGET_KEYS:
        value = route_values.get(key)
        if value:
            return key.removesuffix("_id"), value
    blueprint = endpoint.split(".", 1)[0] if "." in endpoint else endpoint
    return f"endpoint:{blueprint}", endpoint


def audit_mutation_response(response):
    """Record one safe event for every attributable state-changing request."""
    method = request.method.upper()
    endpoint = (request.endpoint or "unknown")[:180]
    if method not in MUTATING_METHODS and endpoint not in STATE_CHANGING_GET_ENDPOINTS:
        return response
    actor = _actor_for_response(int(response.status_code), endpoint)
    if actor is None:
        return response

    if response.status_code < 400:
        outcome = "success"
    elif response.status_code in {401, 403}:
        outcome = "denied"
    else:
        outcome = "failed"

    if request.mimetype in FORM_MIMETYPES:
        form_fields = sorted({str(key)[:80] for key in request.form.keys() if _safe_name(key)})[:100]
        file_fields = sorted({str(key)[:80] for key in request.files.keys() if _safe_name(key)})[:50]
    else:
        form_fields = []
        file_fields = []
    route_values = _safe_route_values(request.view_args)
    identity = system_info(include_request=True)
    detail = {
        "method": method,
        "endpoint": endpoint,
        "route": str(request.url_rule.rule)[:300] if request.url_rule is not None else "",
        "status": int(response.status_code),
        "request_id": str(getattr(g, "request_id", ""))[:80],
        "route_values": route_values,
        "form_fields": form_fields,
        "file_fields": file_fields,
        "content_type": str(request.mimetype or "")[:120],
        "application_id": identity["application_id"],
        "server_name": identity["server_name"],
        "client_ip": identity.get("client_ip", ""),
        "user_agent": identity.get("user_agent", ""),
    }
    action = _semantic_action(endpoint, method)
    target_type, target_id = _target(endpoint, route_values, actor)
    try:
        audit(action, target_type, target_id, outcome=outcome, detail=detail, actor=actor)
    except Exception:
        try:
            from flask import current_app
            current_app.logger.exception("Failed to persist mutation audit event")
        except Exception:
            pass
    return response
