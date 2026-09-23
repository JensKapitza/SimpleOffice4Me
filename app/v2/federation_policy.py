"""Local deny-first policy for federation routes and scopes."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..federation_attestations import FederationAttestationStore
from .authorization import AuthorizationStore, GrantRight

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
    denied_relays: tuple[str, ...] | None = None


@dataclass(frozen=True)
class TrustRequirement:
    target_peer: str
    scope: str
    verifier_peers: tuple[str, ...]
    quorum: int


class FederationPolicyStore:
    """Persist local peer blocks without publishing them as trust claims."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        control = self.root / ".simpleoffice-v2"
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
                CREATE TABLE IF NOT EXISTS trust_requirement(
                    target_peer TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    verifier_peers_json TEXT NOT NULL,
                    quorum INTEGER NOT NULL,
                    created_by TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(target_peer, scope),
                    CHECK(quorum >= 1 AND quorum <= 32)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(route_constraint)").fetchall()
            }
            if "denied_relays_json" not in columns:
                db.execute(
                    "ALTER TABLE route_constraint ADD COLUMN denied_relays_json TEXT"
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
        denied_relays: Iterable[str] | None = None,
        created_by: str = "",
    ) -> RouteConstraint:
        target = self._peer(target_peer)
        checked_scope = self._scope(scope)
        if not isinstance(direct_only, bool):
            raise ValueError("direct_only must be boolean")
        if isinstance(max_hops, bool):
            raise ValueError("max federation route hops must be an integer")
        if max_hops is not None:
            max_hops = int(max_hops)
            if max_hops < 1 or max_hops > 32:
                raise ValueError("max federation route hops must be in range 1..32")
        normalized_relays = None
        if allowed_relays is not None:
            if isinstance(allowed_relays, (str, bytes)):
                raise ValueError("allowed relay peers must be an iterable of peer ids")
            normalized_relays = tuple(sorted({
                self._peer(peer) for peer in allowed_relays
                if str(peer or "").strip()
            }))
        normalized_denied_relays = None
        if denied_relays is not None:
            if isinstance(denied_relays, (str, bytes)):
                raise ValueError("denied relay peers must be an iterable of peer ids")
            normalized_denied_relays = tuple(sorted({
                self._peer(peer) for peer in denied_relays
                if str(peer or "").strip()
            }))
        if (
            normalized_relays is not None
            and normalized_denied_relays is not None
            and set(normalized_relays).intersection(normalized_denied_relays)
        ):
            raise ValueError("a relay peer cannot be both allowed and denied")
        if (
            not direct_only
            and max_hops is None
            and normalized_relays is None
            and normalized_denied_relays is None
        ):
            raise ValueError("route constraint must restrict at least one route property")
        encoded_relays = (
            None if normalized_relays is None
            else json.dumps(list(normalized_relays), separators=(",", ":"))
        )
        encoded_denied_relays = (
            None if normalized_denied_relays is None
            else json.dumps(list(normalized_denied_relays), separators=(",", ":"))
        )
        with self._db() as db:
            db.execute(
                """INSERT INTO route_constraint(
                       target_peer,scope,direct_only,max_hops,allowed_relays_json,
                       denied_relays_json,created_by,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(target_peer,scope) DO UPDATE SET
                       direct_only=excluded.direct_only,
                       max_hops=excluded.max_hops,
                       allowed_relays_json=excluded.allowed_relays_json,
                       denied_relays_json=excluded.denied_relays_json,
                       created_by=excluded.created_by,
                       updated_at=excluded.updated_at""",
                (
                    target, checked_scope, 1 if direct_only else 0, max_hops,
                    encoded_relays, encoded_denied_relays,
                    str(created_by)[:160], int(time.time()),
                ),
            )
        return RouteConstraint(
            target_peer=target,
            scope=checked_scope,
            direct_only=bool(direct_only),
            max_hops=max_hops,
            allowed_relays=normalized_relays,
            denied_relays=normalized_denied_relays,
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
        with self._db() as db:
            if checked_scope == "all":
                rows = db.execute(
                    """SELECT * FROM route_constraint
                       WHERE target_peer=? AND scope='all'
                       ORDER BY CASE WHEN scope='all' THEN 0 ELSE 1 END""",
                    (target,),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM route_constraint
                       WHERE target_peer=? AND scope IN ('all', ?)
                       ORDER BY CASE WHEN scope='all' THEN 0 ELSE 1 END""",
                    (target, checked_scope),
                ).fetchall()
        result: list[RouteConstraint] = []
        for row in rows:
            raw_relays = row["allowed_relays_json"]
            raw_denied_relays = row["denied_relays_json"]
            relays = None
            denied_relays = None
            try:
                if raw_relays is not None:
                    value = json.loads(str(raw_relays))
                    if not isinstance(value, list):
                        raise ValueError("relay allowlist is not a list")
                    relays = tuple(self._peer(item) for item in value)
                if raw_denied_relays is not None:
                    denied_value = json.loads(str(raw_denied_relays))
                    if not isinstance(denied_value, list):
                        raise ValueError("relay denylist is not a list")
                    denied_relays = tuple(self._peer(item) for item in denied_value)
                if (
                    relays is not None
                    and denied_relays is not None
                    and set(relays).intersection(denied_relays)
                ):
                    raise ValueError("relay allowlist and denylist overlap")
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RuntimeError("stored federation route constraint is invalid") from exc
            result.append(RouteConstraint(
                target_peer=target,
                scope=str(row["scope"]),
                direct_only=bool(row["direct_only"]),
                max_hops=int(row["max_hops"]) if row["max_hops"] is not None else None,
                allowed_relays=relays,
                denied_relays=denied_relays,
            ))
        return result

    def set_trust_requirement(
        self,
        target_peer: str,
        *,
        scope: str = "all",
        verifier_peers: Iterable[str],
        quorum: int = 1,
        created_by: str = "",
    ) -> TrustRequirement:
        target = self._peer(target_peer)
        checked_scope = self._scope(scope)
        if isinstance(verifier_peers, (str, bytes)):
            raise ValueError("verifier peers must be an iterable of peer ids")
        normalized = tuple(sorted({
            self._peer(peer)
            for peer in verifier_peers
            if str(peer or "").strip()
        }))
        if not normalized:
            raise ValueError("at least one verifier peer is required")
        if target in normalized:
            raise ValueError("target peer cannot confirm itself")
        if isinstance(quorum, bool):
            raise ValueError("confirmation quorum must be an integer")
        quorum = int(quorum)
        if quorum < 1 or quorum > len(normalized):
            raise ValueError("confirmation quorum must be between 1 and verifier count")
        with self._db() as db:
            db.execute(
                """INSERT INTO trust_requirement(
                       target_peer,scope,verifier_peers_json,quorum,created_by,updated_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(target_peer,scope) DO UPDATE SET
                       verifier_peers_json=excluded.verifier_peers_json,
                       quorum=excluded.quorum,
                       created_by=excluded.created_by,
                       updated_at=excluded.updated_at""",
                (
                    target,
                    checked_scope,
                    json.dumps(list(normalized), separators=(",", ":")),
                    quorum,
                    str(created_by)[:160],
                    int(time.time()),
                ),
            )
        return TrustRequirement(target, checked_scope, normalized, quorum)

    def clear_trust_requirement(self, target_peer: str, *, scope: str = "all") -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM trust_requirement WHERE target_peer=? AND scope=?",
                (self._peer(target_peer), self._scope(scope)),
            )

    def trust_requirements(self, target_peer: str, *, scope: str) -> list[TrustRequirement]:
        target = self._peer(target_peer)
        checked_scope = self._scope(scope)
        with self._db() as db:
            if checked_scope == "all":
                rows = db.execute(
                    """SELECT * FROM trust_requirement
                       WHERE target_peer=? AND scope='all'
                       ORDER BY scope""",
                    (target,),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM trust_requirement
                       WHERE target_peer=? AND scope IN ('all', ?)
                       ORDER BY CASE WHEN scope='all' THEN 0 ELSE 1 END""",
                    (target, checked_scope),
                ).fetchall()
        result: list[TrustRequirement] = []
        for row in rows:
            try:
                raw = json.loads(str(row["verifier_peers_json"]))
                if not isinstance(raw, list) or not raw:
                    raise ValueError("verifier list is invalid")
                verifiers = tuple(sorted({self._peer(peer) for peer in raw}))
                quorum = int(row["quorum"])
                if target in verifiers or quorum < 1 or quorum > len(verifiers):
                    raise ValueError("stored confirmation quorum is invalid")
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RuntimeError("stored federation trust requirement is invalid") from exc
            result.append(TrustRequirement(
                target_peer=target,
                scope=str(row["scope"]),
                verifier_peers=verifiers,
                quorum=quorum,
            ))
        return result

    def _blocked_capability_path_decision(
        self,
        *,
        target_peer: str,
        scope: str,
        blocked_peers: set[str],
        authorization_store: AuthorizationStore | None,
        object_refs: Iterable[str] | None,
        now: int | None,
    ) -> PolicyDecision:
        if not blocked_peers:
            return PolicyDecision(True, "allowed", scope=scope)
        auth = authorization_store
        if auth is None:
            auth_path = self.root / ".simpleoffice-v2" / "authorization.sqlite3"
            if not auth_path.is_file():
                return PolicyDecision(True, "allowed", scope=scope)
            auth = AuthorizationStore(self.root)

        for blocked_peer in sorted(blocked_peers):
            if auth.has_effective_relationship(
                issuer=target_peer,
                subject=blocked_peer,
                rights=(GrantRight.RELAY, GrantRight.DELEGATE),
                object_refs=object_refs,
                now=now,
            ):
                return PolicyDecision(
                    False,
                    "blocked_downstream_capability",
                    blocked_peer,
                    scope,
                )
        return PolicyDecision(True, "allowed", scope=scope)

    def _trust_requirement_decision(
        self,
        *,
        target_peer: str,
        scope: str,
        now: int | None,
        blocked_verifiers: set[str],
    ) -> PolicyDecision:
        requirements = self.trust_requirements(target_peer, scope=scope)
        if not requirements:
            return PolicyDecision(True, "allowed", scope=scope)
        attestations = FederationAttestationStore(self.root)
        for requirement in requirements:
            allowed_verifiers = tuple(
                peer for peer in requirement.verifier_peers
                if peer not in blocked_verifiers
            )
            if len(allowed_verifiers) < requirement.quorum:
                return PolicyDecision(
                    False,
                    "confirmation_quorum_missing",
                    scope=scope,
                )
            confirmations = attestations.valid_confirmations(
                target_peer,
                allowed_verifiers,
                now=now,
            )
            confirmed = {
                str(item.get("verifier_peer_id") or "")
                for item in confirmations
            }
            if len(confirmed) < requirement.quorum:
                return PolicyDecision(
                    False,
                    "confirmation_quorum_missing",
                    scope=scope,
                )
        return PolicyDecision(True, "allowed", scope=scope)

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
            if constraint.denied_relays is not None:
                denied_set = set(constraint.denied_relays)
                denied = next((peer for peer in relays if peer in denied_set), "")
                if denied:
                    return PolicyDecision(False, "relay_explicitly_denied", denied, scope)
            if constraint.allowed_relays is not None:
                allowed = set(constraint.allowed_relays)
                denied = next((peer for peer in relays if peer not in allowed), "")
                if denied:
                    return PolicyDecision(False, "relay_not_allowed", denied, scope)
        return PolicyDecision(True, "allowed", scope=scope)

    def decision(self, route: list[str] | tuple[str, ...], *, scope: str,
                 now: int | None = None, require_known_route: bool = True,
                 target_peer: str | None = None,
                 authorization_store: AuthorizationStore | None = None,
                 object_refs: Iterable[str] | None = None) -> PolicyDecision:
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
        if target_peer is not None and not str(target_peer or "").strip():
            return PolicyDecision(False, "route_target_unknown", scope=checked_scope)
        target = self._peer(target_peer) if target_peer is not None else peers[-1]
        route_decision = self._route_constraint_decision(
            peers,
            target_peer=target,
            scope=checked_scope,
        )
        if not route_decision.allowed:
            return route_decision
        blocked_peers = {
            str(block["peer_id"])
            for block in blocks
            if block["scope"] in {"all", checked_scope}
        }
        capability_decision = self._blocked_capability_path_decision(
            target_peer=target,
            scope=checked_scope,
            blocked_peers=blocked_peers,
            authorization_store=authorization_store,
            object_refs=object_refs,
            now=now,
        )
        if not capability_decision.allowed:
            return capability_decision
        return self._trust_requirement_decision(
            target_peer=target,
            scope=checked_scope,
            now=now,
            blocked_verifiers=blocked_peers,
        )
