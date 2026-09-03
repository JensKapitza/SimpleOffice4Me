"""Central account roles, feature gates and privacy-preserving audit events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from flask import g, has_request_context

from .db import get_db

FEATURES = {
    "documents": "Dokumente und Suche",
    "calendar": "Kalender und CalDAV-Einstellungen",
    "contacts": "Kontakte und CardDAV-Einstellungen",
    "mail": "IMAP, Sieve und SMTP",
    "webdav": "WebDAV-Einstellungen",
    "sync": "Synchronisation und Replikation",
    "projects": "Projekte und Zeiterfassung",
    "datalogger": "Datenlogger und Sensoren",
}

_SENSITIVE_DETAIL_PARTS = (
    "password", "passwd", "secret", "token", "authorization", "cookie",
    "api_key", "apikey", "private_key", "credential",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_admin(user=None) -> bool:
    user = user if user is not None else getattr(g, "user", None)
    return bool(user and user["is_admin"] and not user["is_disabled"])


def has_feature(user, feature: str) -> bool:
    """Return a feature decision and fail closed for unknown/invalid features."""
    feature = str(feature or "").strip()
    if not user or feature not in FEATURES:
        return False
    if bool(user["is_disabled"]):
        return False
    if is_admin(user):
        return True
    row = get_db().execute(
        "SELECT enabled FROM user_permission WHERE user_id = ? AND feature = ?",
        (user["id"], feature),
    ).fetchone()
    return row is None or bool(row["enabled"])


def permissions_for(user_id: int) -> dict[str, bool]:
    rows = get_db().execute(
        "SELECT feature, enabled FROM user_permission WHERE user_id = ?", (user_id,)
    ).fetchall()
    explicit = {
        row["feature"]: bool(row["enabled"])
        for row in rows
        if row["feature"] in FEATURES
    }
    return {feature: explicit.get(feature, True) for feature in FEATURES}


def safe_delta(before: dict, after: dict, *, allowed: set[str] | None = None) -> dict:
    keys = sorted(set(before) | set(after))
    if allowed is not None:
        keys = [key for key in keys if key in allowed]
    changes = {}
    for key in keys:
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[str(key)[:80]] = {"before": old, "after": new}
    return changes


def _sensitive_detail_key(key: object) -> bool:
    folded = str(key).casefold().replace("-", "_")
    return any(part in folded for part in _SENSITIVE_DETAIL_PARTS)


def _redact_detail(value: Any, *, depth: int = 0) -> Any:
    """Redact credential-shaped audit fields without discarding useful context."""
    if depth > 5:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 200:
                result["..."] = "[TRUNCATED]"
                break
            safe_key = str(key)[:120]
            result[safe_key] = "[REDACTED]" if _sensitive_detail_key(key) else _redact_detail(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        rows = list(value)
        return [_redact_detail(item, depth=depth + 1) for item in rows[:200]]
    if isinstance(value, bytes):
        return f"[BYTES:{len(value)}]"
    if isinstance(value, str):
        return value[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4000]


def audit(action: str, target_type: str, target_id: str = "", outcome: str = "success", detail: dict | None = None, actor=None) -> None:
    actor = actor if actor is not None else (getattr(g, "user", None) if has_request_context() else None)
    safe_detail = _redact_detail(dict(detail or {}))
    if has_request_context():
        from .system_identity import system_info
        identity = system_info(include_request=True)
        for key in ("application_id", "server_name", "client_ip", "user_agent", "request_id"):
            safe_detail.setdefault(key, identity.get(key, ""))
    get_db().execute(
        """INSERT INTO security_event(
               occurred_at, actor_id, actor_name, action, target_type, target_id, outcome, detail
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (utc_now(), actor["id"] if actor else None, actor["username"] if actor else None,
         str(action)[:160], str(target_type)[:160], str(target_id)[:500], str(outcome)[:80],
         json.dumps(safe_detail, ensure_ascii=False, sort_keys=True)),
    )
    get_db().commit()


def activity_for(target_type: str, target_id: str, *, limit: int = 200):
    return get_db().execute(
        """SELECT * FROM security_event
           WHERE target_type = ? AND target_id = ?
           ORDER BY occurred_at DESC LIMIT ?""",
        (target_type, str(target_id), min(500, max(1, int(limit)))),
    ).fetchall()


def error_fingerprint(exception_type: str, endpoint: str, method: str, path: str) -> str:
    value = "\0".join((exception_type, endpoint, method, path)).encode("utf-8", "replace")
    return hashlib.sha256(value).hexdigest()[:20]
