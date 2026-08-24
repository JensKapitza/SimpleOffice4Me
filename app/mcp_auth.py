"""Short-lived, revocable bearer credentials for the MCP endpoint."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .db import get_db


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def create_token(user_id: int, name: str, can_write: bool, days: int = 30) -> tuple[str, dict]:
    name = name.strip()[:80]
    if not name:
        raise ValueError("Ein Name für den MCP-Zugang ist erforderlich.")
    days = max(1, min(int(days), 365))
    secret = "so_mcp_" + secrets.token_urlsafe(40)
    digest = hashlib.sha256(secret.encode()).hexdigest()
    created = _now()
    expires = created + timedelta(days=days)
    db = get_db()
    cursor = db.execute(
        "INSERT INTO mcp_token(user_id,name,token_hash,token_prefix,can_write,created_at,expires_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, name, digest, secret[:15], int(can_write), created.isoformat(), expires.isoformat()),
    )
    db.commit()
    return secret, {"id": cursor.lastrowid, "name": name, "can_write": can_write, "expires_at": expires.isoformat()}


def authenticate_token(secret: str):
    if not secret.startswith("so_mcp_") or len(secret) > 256:
        return None
    digest = hashlib.sha256(secret.encode()).hexdigest()
    row = get_db().execute(
        """SELECT mcp_token.id AS token_id, mcp_token.name, mcp_token.can_write,
                  mcp_token.expires_at, mcp_token.revoked_at,
                  user.id AS id, user.username, user.is_admin, user.is_disabled, user.auth_version
           FROM mcp_token JOIN user ON user.id = mcp_token.user_id
           WHERE token_hash = ?""", (digest,),
    ).fetchone()
    if row is None or row["revoked_at"] or row["is_disabled"]:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) <= _now():
            return None
    except ValueError:
        return None
    get_db().execute("UPDATE mcp_token SET last_used_at = ? WHERE id = ?", (_now().isoformat(), row["token_id"]))
    get_db().commit()
    return row


def tokens_for_user(user_id: int):
    return get_db().execute(
        "SELECT id,name,token_prefix,can_write,created_at,expires_at,last_used_at,revoked_at FROM mcp_token WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    ).fetchall()


def revoke_token(user_id: int, token_id: int) -> bool:
    cursor = get_db().execute(
        "UPDATE mcp_token SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
        (_now().isoformat(), token_id, user_id),
    )
    get_db().commit()
    return cursor.rowcount == 1


def operation_log(user_id: int, administrator: bool, limit: int = 200):
    where = "" if administrator else "WHERE mcp_operation.actor_id = ?"
    parameters = () if administrator else (user_id,)
    return get_db().execute(
        f"""SELECT mcp_operation.request_id,mcp_operation.occurred_at,user.username,
                   mcp_operation.tool,mcp_operation.target_id,mcp_operation.outcome,mcp_operation.error_type
            FROM mcp_operation JOIN user ON user.id=mcp_operation.actor_id
            {where} ORDER BY mcp_operation.id DESC LIMIT {max(1, min(int(limit), 500))}""",
        parameters,
    ).fetchall()
