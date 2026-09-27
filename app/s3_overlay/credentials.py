"""Credential lifecycle for S3 SigV4 access keys."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from flask import current_app


def _path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / ".simpleoffice-meta" / "s3-overlay.sqlite3"


def _key() -> bytes:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"simpleoffice-s3-credentials-v1", info=b"sigv4-secret-encryption").derive(raw)


def _db() -> sqlite3.Connection:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.parent.chmod(0o700)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("""CREATE TABLE IF NOT EXISTS s3_credential(
        access_key TEXT PRIMARY KEY, username TEXT NOT NULL, label TEXT NOT NULL,
        scopes TEXT NOT NULL, prefix TEXT NOT NULL, nonce BLOB NOT NULL,
        encrypted_secret BLOB NOT NULL, created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
        last_used_at INTEGER NOT NULL DEFAULT 0
    )""")
    if os.name == "posix":
        path.chmod(0o600)
    return db


def create(username: str, label: str, scopes: list[str], prefix: str, expires_days: int) -> dict:
    label = " ".join(str(label).split()).strip()
    allowed = {"read", "inbox:put"}
    scopes = sorted(set(scopes))
    if not label or len(label) > 80 or any(ord(ch) < 32 for ch in label):
        raise ValueError("Bezeichnung muss 1 bis 80 druckbare Zeichen enthalten.")
    if not scopes or not set(scopes) <= allowed:
        raise ValueError("Ungültiger S3-Rechteumfang.")
    if isinstance(expires_days, bool) or not 1 <= int(expires_days) <= 365:
        raise ValueError("Gültigkeit muss zwischen 1 und 365 Tagen liegen.")
    if len(prefix) > 300 or any(ord(ch) < 32 for ch in prefix) or ".." in prefix.split("/"):
        raise ValueError("Ungültiger Prefix.")
    access = "SO" + secrets.token_hex(16).upper()
    secret = secrets.token_urlsafe(36)
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_key()).encrypt(nonce, secret.encode(), access.encode())
    now = int(time.time())
    db = _db()
    try:
        active = db.execute("SELECT COUNT(*) FROM s3_credential WHERE username=? AND revoked=0 AND expires_at>?", (username, now)).fetchone()[0]
        if active >= 10:
            raise ValueError("Höchstens 10 aktive S3-Zugänge sind erlaubt.")
        db.execute("INSERT INTO s3_credential(access_key,username,label,scopes,prefix,nonce,encrypted_secret,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (access, username, label, json.dumps(scopes), prefix.strip("/"), nonce, encrypted, now, now + int(expires_days) * 86400))
        db.commit()
    finally:
        db.close()
    return {"access_key": access, "secret_key": secret, "label": label, "scopes": scopes, "prefix": prefix.strip("/"), "expires_at": now + int(expires_days) * 86400}


def get(access_key: str, *, include_secret: bool = False) -> dict | None:
    db = _db()
    try:
        row = db.execute("SELECT * FROM s3_credential WHERE access_key=? AND revoked=0 AND expires_at>?", (access_key, int(time.time()))).fetchone()
    finally:
        db.close()
    if row is None:
        return None
    result = {"access_key": row["access_key"], "username": row["username"], "label": row["label"],
              "scopes": json.loads(row["scopes"]), "prefix": row["prefix"], "expires_at": row["expires_at"]}
    if include_secret:
        result["secret_key"] = AESGCM(_key()).decrypt(bytes(row["nonce"]), bytes(row["encrypted_secret"]), access_key.encode()).decode()
    return result


def mark_used(access_key: str) -> None:
    db = _db()
    try:
        now = int(time.time())
        db.execute("UPDATE s3_credential SET last_used_at=? WHERE access_key=? AND last_used_at<?", (now, access_key, now - 900))
        db.commit()
    finally:
        db.close()


def list_for(username: str) -> list[dict]:
    db = _db()
    try:
        rows = db.execute("SELECT access_key,label,scopes,prefix,created_at,expires_at,revoked,last_used_at FROM s3_credential WHERE username=? ORDER BY created_at DESC", (username,)).fetchall()
    finally:
        db.close()
    return [{"access_key": row["access_key"], "label": row["label"], "scopes": json.loads(row["scopes"]), "prefix": row["prefix"], "created_at": datetime.fromtimestamp(row["created_at"], timezone.utc).isoformat(), "expires_at": datetime.fromtimestamp(row["expires_at"], timezone.utc).isoformat(), "revoked": bool(row["revoked"]), "last_used_at": datetime.fromtimestamp(row["last_used_at"], timezone.utc).isoformat() if row["last_used_at"] else ""} for row in rows]


def revoke(username: str, access_key: str) -> bool:
    db = _db()
    try:
        changed = db.execute("UPDATE s3_credential SET revoked=1, encrypted_secret=x'', nonce=x'' WHERE username=? AND access_key=? AND revoked=0", (username, access_key)).rowcount
        db.commit()
        return bool(changed)
    finally:
        db.close()
