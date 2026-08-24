"""Central account roles, feature gates and privacy-preserving audit events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from flask import g

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


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_admin(user=None) -> bool:
    user = user if user is not None else getattr(g, "user", None)
    return bool(user and user["is_admin"] and not user["is_disabled"])


def has_feature(user, feature: str) -> bool:
    if is_admin(user):
        return True
    row = get_db().execute(
        "SELECT enabled FROM user_permission WHERE user_id = ? AND feature = ?",
        (user["id"], feature),
    ).fetchone()
    # Backwards compatible: an absent rule preserves the account's previous
    # access; administrators can create an explicit denial.
    return row is None or bool(row["enabled"])


def permissions_for(user_id: int) -> dict[str, bool]:
    rows = get_db().execute(
        "SELECT feature, enabled FROM user_permission WHERE user_id = ?", (user_id,)
    ).fetchall()
    explicit = {row["feature"]: bool(row["enabled"]) for row in rows}
    return {feature: explicit.get(feature, True) for feature in FEATURES}


def audit(action: str, target_type: str, target_id: str = "", outcome: str = "success", detail: dict | None = None, actor=None) -> None:
    actor = actor if actor is not None else getattr(g, "user", None)
    # Detail is deliberately constrained by callers to identifiers and state;
    # credentials and request bodies never enter this table.
    get_db().execute(
        """INSERT INTO security_event(
               occurred_at, actor_id, actor_name, action, target_type, target_id, outcome, detail
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (utc_now(), actor["id"] if actor else None, actor["username"] if actor else None,
         action, target_type, str(target_id), outcome,
         json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)),
    )
    get_db().commit()


def error_fingerprint(exception_type: str, endpoint: str, method: str, path: str) -> str:
    value = "\0".join((exception_type, endpoint, method, path)).encode("utf-8", "replace")
    return hashlib.sha256(value).hexdigest()[:20]
