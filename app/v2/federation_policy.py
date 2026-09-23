"""Local deny-first policy for federation routes and scopes."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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


@dataclass(frozen=True)
class RouteConstraint:
    target_peer: str
    scope: str
    direct_only: bool = False
    max_hops: int | None = None
    allowed_relays: tuple[str, ...] | None = None


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
                CREATE TABLE IF NOT EXISTS route_constraint(
                    target_peer TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    direct_only INTEGER NOT NULL DEFAULT 0,
                    max_hops INTEGER,
                    allowed_relays_json TEXT,
                    created_by TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(target_peer, scope),
                    CHECK(direct_only IN (0,1)),
                    CHECK(max_hops IS NULL OR (max_hops >= 1 AND max_hops <= 32))
                );
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

    def set_route_constraint(
        self,
        target_peer: str,
        *,
        scope: str = "all",
        direct_only: bool = False,
        max_hops: int | None = None,
        allowed_relays: Iterable[str] | None = None,
        created_by: str = "",
    ) -> RouteConstraint:
        target = self._peer(target_peer)
        checked_scope = self._scope(scope)
        if max_hops is not None:
            max_hops = int(max_hops)
            if max_hops < 1 or max_hops > 32:
                raise ValueError("max federation route hops must be in range 1..32")
        normalized_relays = None
        if allowed_relays is not None:
            normalized_relays = tuple(sorted({
                self._peer(peer) for peer in allowed_relays
                if str(peer or "").strip()
            }))
        if not direct_only and max_hops is None and normalized_relays is None:
            raise ValueError("route constraint must restrict at least one route property")
        encoded_relays = (
            None if normalized_relays is None
            else json.dumps(list(normalized_relays), separators=(",", ":"))
        )
        with self._db() as db:
            db.execute(
                """INSERT INTO route_constraint(
                       target_peer,scope,direct_only,max_hops,allowed_relays_json,
                       created_by,updated_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(target_peer,scope) DO UPDATE SET
                       direct_only=excluded.direct_only,
                       max_hops=excluded.max_hops,
                       allowed_relays_json=excluded.allowed_relays_json,
                       created_by=excluded.created_by,
                       updated_at=excluded.updated_at""",
                (
                    target, checked_scope, 1 if direct_only else 0, max_hops,
                    encoded_relays, str(created_by)[:160], int(time.time()),
                ),
            )
        return RouteConstraint(
            target_peer=target,
            scope=checked_scope,
            direct_only=bool(direct_only),
            max_hops=max_hops,
            allowed_relays=normalized_relays,
        )

    def clear_route_constraint(self, target_peer: str, *, scope: str = "all") -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM route_constraint WHERE target_peer=? AND scope=?",
                (self._peer(target_peer), self._scope(scope)),
            )

    def route_constraints(self, target_peer: str, *, scope: str) -> list[RouteConstraint]:
        target = self._peer(target_peer)
        checked_scope = self._scope(scope)
        scopes = ("all",) if checked_scope == "all" else ("all", checked_scope)
        placeholders = ",".join("?" for _ in scopes)
        with self._db() as db:
            rows = db.execute(
                f"""SELECT * FROM route_constraint
                    WHERE target_peer=? AND scope IN ({placeholders})
                    ORDER BY CASE WHEN scope='all' THEN 0 ELSE 1 END""",
                (target, *scopes),
            ).fetchall()
        result: list[RouteConstraint] = []
        for row in rows:
            raw_relays = row["allowed_relays_json"]
            relays = None
            if raw_relays is not None:
                try:
                    value = json.loads(str(raw_relays))
                    if not isinstance(value, list):
                        raise ValueError("relay allowlist is not a list")
                    relays = tuple(self._peer(item) for item in value)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise RuntimeError("stored federation route constraint is invalid") from exc
            result.append(RouteConstraint(
                target_peer=target,
                scope=str(row["scope"]),
                direct_only=bool(row["direct_only"]),
                max_hops=int(row["max_hops"]) if row["max_hops"] is not None else None,
                allowed_relays=relays,
            ))
        return result

    def _route_constraint_decision(
        self,
        peers: list[str],
        *,
        target_peer: str,
        scope: str,
    ) -> PolicyDecision:
        target = self._peer(target_peer)
        if target not in peers:
            return PolicyDecision(False, "route_target_unknown", target, scope)
        if len(set(peers)) != len(peers):
            return PolicyDecision(False, "route_cycle", scope=scope)
        constraints = self.route_constraints(target, scope=scope)
        if not constraints:
            return PolicyDecision(True, "allowed", scope=scope)

        hop_count = max(0, len(peers) - 1)
        relays = [peer for index, peer in enumerate(peers) if index > 0 and peer != target]
        for constraint in constraints:
            if constraint.direct_only and (hop_count != 1 or relays):
                return PolicyDecision(False, "direct_only", scope=scope)
            if constraint.max_hops is not None and hop_count > constraint.max_hops:
                return PolicyDecision(False, "max_hops_exceeded", scope=scope)
            if constraint.allowed_relays is not None:
                allowed = set(constraint.allowed_relays)
                denied = next((peer for peer in relays if peer not in allowed), "")
                if denied:
                    return PolicyDecision(False, "relay_not_allowed", denied, scope)
        return PolicyDecision(True, "allowed", scope=scope)

    def decision(self, route: list[str] | tuple[str, ...], *, scope: str,
                 now: int | None = None, require_known_route: bool = True,
                 target_peer: str | None = None) -> PolicyDecision:
        checked_scope = self._scope(scope)
        peers = [self._peer(peer) for peer in route if str(peer or "").strip()]
        if require_known_route and not peers:
            return PolicyDecision(False, "route_unknown", scope=checked_scope)
        blocks = self.active_blocks(now=now)
        for peer in peers:
            for block in blocks:
                if block["peer_id"] == peer and block["scope"] in {"all", checked_scope}:
                    return PolicyDecision(False, "explicit_block", peer, checked_scope)
        if not peers:
            return PolicyDecision(True, "allowed", scope=checked_scope)
        target = self._peer(target_peer) if target_peer is not None else peers[-1]
        return self._route_constraint_decision(
            peers,
            target_peer=target,
            scope=checked_scope,
        )
