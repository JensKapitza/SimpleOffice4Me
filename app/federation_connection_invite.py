"""One-scan, one-time invitations for establishing a peer-specific token."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Any

from .federation_store import FederationStore
from .security_controls import protect_value, unprotect_value

INVITE_TTL_SECONDS = 600
INVITE_ID_BYTES = 18
INVITE_SECRET_BYTES = 32


class FederationConnectionInvites:
    """Store only an encrypted short-lived invite secret and consume it once."""

    def __init__(self, root: str | Path):
        self.store = FederationStore(root)
        with self.store._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS federation_connection_invite(
                    invite_id TEXT PRIMARY KEY,
                    secret_enc TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    claimed_at INTEGER,
                    claimed_peer_id TEXT NOT NULL DEFAULT '',
                    claimed_fingerprint TEXT NOT NULL DEFAULT ''
                )"""
            )
            columns = {row["name"] for row in db.execute(
                "PRAGMA table_info(federation_connection_invite)"
            ).fetchall()}
            if "claimed_peer_id" not in columns:
                db.execute("ALTER TABLE federation_connection_invite ADD COLUMN claimed_peer_id TEXT NOT NULL DEFAULT ''")
            if "claimed_fingerprint" not in columns:
                db.execute("ALTER TABLE federation_connection_invite ADD COLUMN claimed_fingerprint TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _aad(invite_id: str) -> str:
        return f"federation-connection-invite:{invite_id}"

    def issue(self, *, now: int | None = None) -> dict[str, Any]:
        created_at = int(time.time()) if now is None else int(now)
        invite_id = secrets.token_urlsafe(INVITE_ID_BYTES)
        secret = secrets.token_urlsafe(INVITE_SECRET_BYTES)
        secret_enc = protect_value(secret, self._aad(invite_id))
        with self.store._db() as db:
            db.execute(
                "DELETE FROM federation_connection_invite WHERE expires_at<?",
                (created_at,),
            )
            db.execute(
                """INSERT INTO federation_connection_invite
                (invite_id,secret_enc,created_at,expires_at,claimed_at) VALUES(?,?,?,?,NULL)""",
                (invite_id, secret_enc, created_at, created_at + INVITE_TTL_SECONDS),
            )
        return {"invite_id": invite_id, "secret": secret,
                "created_at": created_at, "expires_at": created_at + INVITE_TTL_SECONDS}

    def secret(self, invite_id: str, *, claimant: str = "", fingerprint: str = "", now: int | None = None) -> str:
        timestamp = int(time.time()) if now is None else int(now)
        if not isinstance(invite_id, str) or not 20 <= len(invite_id) <= 128:
            raise ValueError("pairing invitation is invalid or expired")
        with self.store._db() as db:
            row = db.execute(
                """SELECT secret_enc FROM federation_connection_invite
                WHERE invite_id=? AND expires_at>=? AND (claimed_at IS NULL OR
                    (claimed_peer_id=? AND claimed_fingerprint=?))""",
                (invite_id, timestamp, str(claimant), str(fingerprint)),
            ).fetchone()
        if row is None:
            raise ValueError("pairing invitation is invalid or expired")
        try:
            return unprotect_value(str(row["secret_enc"]), self._aad(invite_id))
        except (TypeError, ValueError, RuntimeError) as exc:
            raise ValueError("pairing invitation is unavailable") from exc

    def claim(self, invite_id: str, claimant: str, fingerprint: str, *, now: int | None = None) -> bool:
        timestamp = int(time.time()) if now is None else int(now)
        with self.store._db() as db:
            changed = db.execute(
                """UPDATE federation_connection_invite SET claimed_at=?,claimed_peer_id=?,claimed_fingerprint=?
                WHERE invite_id=? AND claimed_at IS NULL AND expires_at>=?""",
                (timestamp, str(claimant)[:128], str(fingerprint)[:256], invite_id, timestamp),
            ).rowcount
            if changed == 1:
                return True
            row = db.execute(
                """SELECT claimed_peer_id,claimed_fingerprint,expires_at FROM federation_connection_invite
                WHERE invite_id=?""",
                (invite_id,),
            ).fetchone()
        return bool(
            row and int(row["expires_at"]) >= timestamp
            and hmac.compare_digest(str(row["claimed_peer_id"]), str(claimant)[:128])
            and hmac.compare_digest(str(row["claimed_fingerprint"]), str(fingerprint)[:256])
        )


def invite_proof(secret: str, invite_id: str, profile: dict[str, Any], nonce: str) -> str:
    """Bind invite possession to the claimant identity and one request nonce."""
    message = "\n".join((
        "simpleoffice-peer-invite-v1", invite_id, str(profile["peer_id"]),
        str(profile["fingerprint"]), str(profile["timestamp"]), nonce,
    )).encode("utf-8")
    return hmac.new(secret.encode("ascii"), message, hashlib.sha256).hexdigest()


def verify_invite_proof(secret: str, invite_id: str, profile: dict[str, Any], nonce: str, supplied: str) -> bool:
    try:
        expected = invite_proof(secret, invite_id, profile, nonce)
    except (KeyError, TypeError, UnicodeEncodeError):
        return False
    return isinstance(supplied, str) and hmac.compare_digest(expected, supplied)


def derive_peer_token(secret: str, peer_a: str, peer_b: str) -> str:
    """Derive the same unique peer token at both ends without sending it."""
    pair = "\n".join(sorted((str(peer_a), str(peer_b)))).encode("utf-8")
    raw = hmac.new(secret.encode("ascii"), b"simpleoffice-peer-token-v1\0" + pair, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
