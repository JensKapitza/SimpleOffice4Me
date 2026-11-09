"""Bounded, encrypted browser capabilities for the read-only repository view."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from flask import current_app

from .db import get_db
from .security_controls import protect_value, unprotect_value

MAX_SESSIONS = 256
START_WINDOW = 600
NETWORK_START_LIMIT = 5
GLOBAL_START_LIMIT = 40


class WorkspaceAuthError(Exception):
    def __init__(self, message: str, status: int = 400, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def session_key(capability: str) -> str:
    return hashlib.sha256(capability.encode("ascii")).hexdigest()


def _db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS repository_workspace_session (
        session_key TEXT PRIMARY KEY, revocation_key TEXT NOT NULL UNIQUE, client_id TEXT NOT NULL,
        context_id TEXT NOT NULL, state TEXT NOT NULL,
        protected TEXT NOT NULL DEFAULT '', login TEXT NOT NULL DEFAULT '',
        expires_at INTEGER NOT NULL, interval INTEGER NOT NULL DEFAULT 5,
        poll_after INTEGER NOT NULL DEFAULT 0,
        lease_id TEXT NOT NULL DEFAULT '', lease_until INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS repository_workspace_throttle (
        key TEXT PRIMARY KEY, window_started INTEGER NOT NULL, count INTEGER NOT NULL
    )""")
    db.commit()
    return db


def _purge(db, now: int) -> None:
    db.execute("DELETE FROM repository_workspace_session WHERE expires_at <= ?", (now,))
    db.execute("DELETE FROM repository_workspace_throttle WHERE window_started <= ?", (now - START_WINDOW,))


def read(key: str, *, now: int | None = None) -> dict | None:
    if not key:
        return None
    now = int(time.time() if now is None else now)
    db = _db()
    _purge(db, now)
    row = db.execute("SELECT * FROM repository_workspace_session WHERE session_key = ?", (key,)).fetchone()
    db.commit()
    return dict(row) if row else None


def _start_keys(remote_addr: str) -> tuple[str, str]:
    secret = str(current_app.config["SECRET_KEY"]).encode()
    digest = hmac.new(secret, remote_addr.encode("utf-8", "replace"), hashlib.sha256).hexdigest()
    return "global", "network:" + digest


def reserve_start(key: str, client_id: str, remote_addr: str, *, now: int | None = None) -> dict:
    """Reserve an OAuth request under cross-worker rate and capacity limits."""
    now = int(time.time() if now is None else now)
    db = _db()
    db.execute("BEGIN IMMEDIATE")
    try:
        _purge(db, now)
        row = db.execute("SELECT * FROM repository_workspace_session WHERE session_key = ?", (key,)).fetchone()
        if row and row["client_id"] == client_id:
            if row["state"] in {"pending", "authenticated"}:
                db.commit()
                return dict(row)
            raise WorkspaceAuthError("Die Anmeldung wird bereits vorbereitet.", 429, 5)
        if row is None and db.execute(
                "SELECT 1 FROM repository_workspace_session WHERE revocation_key=?", (key,)).fetchone():
            raise WorkspaceAuthError("Die Anmeldesitzung wurde erneuert. Bitte die Seite neu laden.", 409)
        for throttle_key, limit in zip(_start_keys(remote_addr), (GLOBAL_START_LIMIT, NETWORK_START_LIMIT)):
            throttle = db.execute("SELECT * FROM repository_workspace_throttle WHERE key = ?", (throttle_key,)).fetchone()
            if throttle and throttle["count"] >= limit:
                raise WorkspaceAuthError("Zu viele Anmeldeversuche. Bitte später erneut versuchen.", 429,
                                         max(1, throttle["window_started"] + START_WINDOW - now))
        count = db.execute("SELECT COUNT(*) FROM repository_workspace_session").fetchone()[0]
        if count >= MAX_SESSIONS and row is None:
            raise WorkspaceAuthError("Die Anmeldung ist vorübergehend ausgelastet.", 503, 60)
        for throttle_key in _start_keys(remote_addr):
            db.execute("""INSERT INTO repository_workspace_throttle VALUES (?, ?, 1)
                ON CONFLICT(key) DO UPDATE SET count=count+1""", (throttle_key, now))
        context_id = secrets.token_hex(16)
        db.execute("""INSERT OR REPLACE INTO repository_workspace_session
            (session_key, revocation_key, client_id, context_id, state, expires_at)
            VALUES (?, ?, ?, ?, 'starting', ?)""", (key, key, client_id, context_id, now + 60))
        db.commit()
        return {"session_key": key, "client_id": client_id, "context_id": context_id, "state": "starting"}
    except Exception:
        db.rollback()
        raise


def protect(key: str, value: dict) -> str:
    return protect_value(json.dumps(value, separators=(",", ":")), "github-workspace:" + key)


def unprotect(row: dict) -> dict:
    protected = row.get("protected", "")
    if not isinstance(protected, str) or not protected.startswith("enc:v1:"):
        raise WorkspaceAuthError("Die GitHub-Sitzung ist ungültig. Bitte erneut anmelden.", 401)
    try:
        value = json.loads(unprotect_value(protected, "github-workspace:" + row["session_key"]))
        if not isinstance(value, dict):
            raise ValueError("Expected object")
    except Exception as exc:
        # Decryption failures must not leak ciphertext, keys or provider payloads.
        raise WorkspaceAuthError("Die GitHub-Sitzung ist ungültig. Bitte erneut anmelden.", 401) from exc
    return value


def finish_start(row: dict, payload: dict, expires_at: int, interval: int, *, now: int | None = None) -> bool:
    now = int(time.time() if now is None else now)
    db = _db()
    updated = db.execute("""UPDATE repository_workspace_session
        SET state='pending', protected=?, expires_at=?, interval=?, poll_after=?
        WHERE session_key=? AND context_id=? AND state='starting' AND expires_at>?""",
        (protect(row["session_key"], payload), expires_at, interval, now + interval,
         row["session_key"], row["context_id"], now)).rowcount
    db.commit()
    return bool(updated)


def reserve_poll(key: str, *, now: int | None = None) -> tuple[dict | None, bool]:
    now = int(time.time() if now is None else now)
    db = _db()
    db.execute("BEGIN IMMEDIATE")
    try:
        _purge(db, now)
        result = db.execute("SELECT * FROM repository_workspace_session WHERE session_key=?", (key,)).fetchone()
        if result is None:
            db.commit()
            return None, False
        row = dict(result)
        if row["state"] != "pending" or row["poll_after"] > now or row["lease_until"] > now:
            db.commit()
            return row, False
        row["lease_id"] = secrets.token_hex(16)
        row["lease_until"] = now + 30
        row["poll_after"] = now + row["interval"]
        db.execute("""UPDATE repository_workspace_session
            SET lease_id=?, lease_until=?, poll_after=? WHERE session_key=?""",
            (row["lease_id"], row["lease_until"], row["poll_after"], key))
        db.commit()
        return row, True
    except Exception:
        db.rollback()
        raise


def finish_poll(row: dict, *, interval: int | None = None, token: str = "", login: str = "",
                expires_at: int = 0, new_key: str = "", now: int | None = None) -> bool:
    now = int(time.time() if now is None else now)
    db = _db()
    if token:
        target_key = new_key or row["session_key"]
        values = ("authenticated", protect(target_key, {"access_token": token}), login,
                  expires_at, 5, 0, secrets.token_hex(16))
    else:
        target_key = row["session_key"]
        interval = row["interval"] if interval is None else interval
        values = ("pending", row["protected"], "", row["expires_at"], interval, now + interval, row["context_id"])
    updated = db.execute("""UPDATE repository_workspace_session
        SET state=?, protected=?, login=?, expires_at=?, interval=?, poll_after=?,
            context_id=?, session_key=?, lease_id='', lease_until=0
        WHERE session_key=? AND context_id=? AND lease_id=? AND expires_at>?""",
        (*values, target_key, row["session_key"], row["context_id"], row["lease_id"], now)).rowcount
    db.commit()
    return bool(updated)


def discard(key: str, context_id: str | None = None) -> None:
    if not key:
        return
    db = _db()
    if context_id is None:
        db.execute("DELETE FROM repository_workspace_session WHERE session_key=?", (key,))
    else:
        db.execute("DELETE FROM repository_workspace_session WHERE session_key=? AND context_id=?", (key, context_id))
    db.commit()


def revoke(key: str) -> dict | None:
    """Revoke even when a concurrent authorization has just rotated its cookie.

    The previous capability is accepted only here, never by read/credentials.
    Serializing with finish_poll makes either commit order fail closed.
    """
    if not key:
        return None
    db = _db()
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("""SELECT * FROM repository_workspace_session
            WHERE session_key=? OR revocation_key=?""", (key, key)).fetchone()
        db.execute("DELETE FROM repository_workspace_session WHERE session_key=? OR revocation_key=?", (key, key))
        db.commit()
        return dict(row) if row else None
    except Exception:
        db.rollback()
        raise
