"""Local deny-first policy for federation routes and scopes."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCOPES = frozenset({
    "all", "documents", "contacts", "calendar", "tasks", "chat",
    "metadata", "storage", "relay", "delegation", "keys/capabilities",
})


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    blocked_peer: str = ""
    scope: str = ""


class FederationPolicyStore:
    """Persist local peer blocks without publishing them as trust claims."""

    def __init__(self, root: str | Path):
        control = Path(root).expanduser().resolve() / ".simpleoffice-v2"
        control.mkdir(parents=True, exist_ok=True)
        self.path = control / "federation-policy.sqlite3"
        self.initialize()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS peer_block(
                    peer_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    PRIMARY KEY(peer_id, scope)
                );
                CREATE INDEX IF NOT EXISTS peer_block_expiry
                    ON peer_block(expires_at);
                """
            )

    @staticmethod
    def _peer(peer_id: str) -> str:
        value = str(peer_id or "").strip()
        if not value or len(value) > 240:
            raise ValueError("invalid peer id")
        return value

    @staticmethod
    def _scope(scope: str) -> str:
        value = str(scope or "all").strip().lower()
        if value not in SCOPES:
            raise ValueError("invalid federation block scope")
        return value

    def block(self, peer_id: str, *, scope: str = "all", reason: str = "",
              created_by: str = "", expires_at: int | None = None) -> None:
        peer = self._peer(peer_id)
        checked_scope = self._scope(scope)
        expiry = int(expires_at) if expires_at is not None else None
        now = int(time.time())
        if expiry is not None and expiry <= now:
            raise ValueError("block expiry must be in the future")
        with self._db() as db:
            db.execute(
                """INSERT INTO peer_block(peer_id,scope,reason,created_by,created_at,expires_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(peer_id,scope) DO UPDATE SET
                   reason=excluded.reason,created_by=excluded.created_by,
                   created_at=excluded.created_at,expires_at=excluded.expires_at""",
                (peer, checked_scope, str(reason)[:500], str(created_by)[:160], now, expiry),
            )

    def unblock(self, peer_id: str, *, scope: str = "all") -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM peer_block WHERE peer_id=? AND scope=?",
                (self._peer(peer_id), self._scope(scope)),
            )

    def active_blocks(self, *, now: int | None = None) -> list[dict]:
        checked_at = int(time.time()) if now is None else int(now)
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM peer_block
                   WHERE expires_at IS NULL OR expires_at>?
                   ORDER BY peer_id,scope""",
                (checked_at,),
            ).fetchall()
        return [dict(row) for row in rows]

    def decision(self, route: list[str] | tuple[str, ...], *, scope: str,
                 now: int | None = None, require_known_route: bool = True) -> PolicyDecision:
        checked_scope = self._scope(scope)
        peers = [self._peer(peer) for peer in route if str(peer or "").strip()]
        if require_known_route and not peers:
            return PolicyDecision(False, "route_unknown", scope=checked_scope)
        blocks = self.active_blocks(now=now)
        for peer in peers:
            for block in blocks:
                if block["peer_id"] == peer and block["scope"] in {"all", checked_scope}:
                    return PolicyDecision(False, "explicit_block", peer, checked_scope)
        return PolicyDecision(True, "allowed", scope=checked_scope)
