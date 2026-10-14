"""Short-lived, one-time pairing codes for explicit federation onboarding."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Any

from .federation_core import sanitize_peer_id
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore


PAIRING_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PAIRING_CODE_LENGTH = 12
DEFAULT_PAIRING_TTL_SECONDS = 10 * 60
MIN_PAIRING_TTL_SECONDS = 60
MAX_PAIRING_TTL_SECONDS = 15 * 60


class FederationPairingTokens:
    """Persist only keyed hashes while binding every code to one expected peer."""

    def __init__(self, root: str | Path, secret_key: str | bytes):
        raw = secret_key.encode("utf-8") if isinstance(secret_key, str) else bytes(secret_key)
        if not raw:
            raise ValueError("pairing secret key is required")
        self._key = hashlib.sha256(b"simpleoffice-federation-pairing\0" + raw).digest()
        self.store = FederationStore(root)
        ensure_schema(self.store)

    @staticmethod
    def _normalize(code: Any) -> str:
        normalized = str(code or "").strip().upper().replace("-", "").replace(" ", "")
        if (
            len(normalized) != PAIRING_CODE_LENGTH
            or any(character not in PAIRING_CODE_ALPHABET for character in normalized)
        ):
            raise ValueError("invalid or expired pairing code")
        return normalized

    def _hash(self, code: Any) -> str:
        normalized = self._normalize(code)
        return hmac.new(self._key, normalized.encode("ascii"), hashlib.sha256).hexdigest()

    @staticmethod
    def _timestamp(now: int | None) -> int:
        return int(time.time()) if now is None else int(now)

    @staticmethod
    def _ttl(value: Any) -> int:
        try:
            ttl = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid pairing code lifetime") from exc
        if ttl < MIN_PAIRING_TTL_SECONDS or ttl > MAX_PAIRING_TTL_SECONDS:
            raise ValueError("invalid pairing code lifetime")
        return ttl

    def issue(
        self,
        expected_peer_id: str,
        ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
        *,
        now: int | None = None,
    ) -> dict[str, Any]:
        peer_id = sanitize_peer_id(expected_peer_id)
        created_at = self._timestamp(now)
        expires_at = created_at + self._ttl(ttl_seconds)
        normalized = "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))
        token_hash = self._hash(normalized)
        with self.store._db() as db:
            db.execute(
                "DELETE FROM federation_pairing_token WHERE expires_at<? OR peer_id=?",
                (created_at, peer_id),
            )
            db.execute(
                """INSERT INTO federation_pairing_token
                (token_hash,peer_id,expires_at,claimed_at,created_at)
                VALUES(?,?,?,?,?)""",
                (token_hash, peer_id, expires_at, None, created_at),
            )
        grouped = "-".join(normalized[index:index + 4] for index in range(0, len(normalized), 4))
        return {"code": grouped, "peer_id": peer_id, "expires_at": expires_at}

    def claim(self, code: Any, claiming_peer_id: str, *, now: int | None = None) -> dict[str, Any]:
        token_hash = self._hash(code)
        peer_id = sanitize_peer_id(claiming_peer_id)
        claimed_at = self._timestamp(now)
        with self.store._db() as db:
            row = db.execute(
                """UPDATE federation_pairing_token SET claimed_at=?
                WHERE token_hash=? AND peer_id=? AND claimed_at IS NULL AND expires_at>=?
                RETURNING expires_at""",
                (claimed_at, token_hash, peer_id, claimed_at),
            ).fetchone()
            if row is None:
                raise ValueError("invalid or expired pairing code")
        return {"peer_id": peer_id, "claimed_at": claimed_at, "expires_at": int(row["expires_at"])}

    def status(self, expected_peer_id: str, *, now: int | None = None) -> dict[str, Any]:
        peer_id = sanitize_peer_id(expected_peer_id)
        timestamp = self._timestamp(now)
        with self.store._db() as db:
            row = db.execute(
                """SELECT expires_at,claimed_at,created_at FROM federation_pairing_token
                WHERE peer_id=? ORDER BY created_at DESC LIMIT 1""",
                (peer_id,),
            ).fetchone()
        if row is None:
            return {"peer_id": peer_id, "status": "missing"}
        if int(row["expires_at"]) < timestamp:
            state = "expired"
        elif row["claimed_at"] is not None:
            state = "claimed"
        else:
            state = "pending"
        return {
            "peer_id": peer_id,
            "status": state,
            "created_at": int(row["created_at"]),
            "expires_at": int(row["expires_at"]),
            "claimed_at": int(row["claimed_at"]) if row["claimed_at"] is not None else None,
        }

    def revoke(self, expected_peer_id: str) -> bool:
        peer_id = sanitize_peer_id(expected_peer_id)
        with self.store._db() as db:
            changed = db.execute(
                "DELETE FROM federation_pairing_token WHERE peer_id=?",
                (peer_id,),
            ).rowcount
        return bool(changed)
