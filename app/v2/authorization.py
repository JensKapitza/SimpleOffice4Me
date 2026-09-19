"""Explicit V2 federation capabilities with scoped delegation and revocation."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class GrantRight(str, Enum):
    READ = "read"
    STORE = "store"
    RELAY = "relay"
    METADATA = "metadata"
    DELEGATE = "delegate"


@dataclass(frozen=True)
class CapabilityGrant:
    grant_id: str
    issuer: str
    subject: str
    rights: frozenset[GrantRight]
    object_refs: frozenset[str]
    expires_at: int
    parent_grant_id: str = ""
    revoked: bool = False

    def __post_init__(self) -> None:
        if not self.grant_id or not self.issuer or not self.subject:
            raise ValueError("grant identity fields are required")
        if not self.rights or not self.object_refs:
            raise ValueError("grant rights and object scope are required")
        if self.expires_at <= 0:
            raise ValueError("grant expiry is required")


class AuthorizationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / ".simpleoffice-v2"
        self.path = self.control / "authorization.sqlite3"
        self.control.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS capability_grant(
                    grant_id TEXT PRIMARY KEY,
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    rights_json TEXT NOT NULL,
                    object_refs_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    parent_grant_id TEXT NOT NULL DEFAULT '',
                    revoked INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS capability_subject_expiry
                    ON capability_grant(subject, expires_at);
                """
            )

    @staticmethod
    def _normalize_rights(rights: Iterable[GrantRight | str]) -> frozenset[GrantRight]:
        normalized = frozenset(
            right if isinstance(right, GrantRight) else GrantRight(str(right))
            for right in rights
        )
        if not normalized:
            raise ValueError("at least one right is required")
        return normalized

    @staticmethod
    def _normalize_scope(object_refs: Iterable[str]) -> frozenset[str]:
        scope = frozenset(str(item).strip() for item in object_refs if str(item).strip())
        if not scope:
            raise ValueError("at least one object reference is required")
        return scope

    def issue_root(
        self,
        *,
        issuer: str,
        subject: str,
        rights: Iterable[GrantRight | str],
        object_refs: Iterable[str],
        expires_at: int,
    ) -> CapabilityGrant:
        grant = CapabilityGrant(
            grant_id=str(uuid.uuid4()),
            issuer=str(issuer).strip(),
            subject=str(subject).strip(),
            rights=self._normalize_rights(rights),
            object_refs=self._normalize_scope(object_refs),
            expires_at=int(expires_at),
        )
        if grant.expires_at <= int(time.time()):
            raise ValueError("grant must expire in the future")
        self._insert(grant)
        return grant

    def delegate(
        self,
        parent_grant_id: str,
        *,
        issuer: str,
        subject: str,
        rights: Iterable[GrantRight | str],
        object_refs: Iterable[str],
        expires_at: int,
        allow_redelegation: bool = False,
    ) -> CapabilityGrant:
        parent = self.get(parent_grant_id)
        if parent is None or not self.is_effective(parent.grant_id):
            raise ValueError("parent grant is unavailable")
        if issuer != parent.subject:
            raise ValueError("only the grant subject may delegate")
        if GrantRight.DELEGATE not in parent.rights:
            raise ValueError("parent grant does not allow delegation")
        child_rights = self._normalize_rights(rights)
        child_scope = self._normalize_scope(object_refs)
        if not child_rights.issubset(parent.rights):
            raise ValueError("delegated rights exceed parent grant")
        if GrantRight.DELEGATE in child_rights and not allow_redelegation:
            raise ValueError("redelegation must be explicitly enabled")
        if not child_scope.issubset(parent.object_refs):
            raise ValueError("delegated object scope exceeds parent grant")
        if int(expires_at) > parent.expires_at:
            raise ValueError("delegated expiry exceeds parent grant")
        grant = CapabilityGrant(
            grant_id=str(uuid.uuid4()),
            issuer=str(issuer),
            subject=str(subject).strip(),
            rights=child_rights,
            object_refs=child_scope,
            expires_at=int(expires_at),
            parent_grant_id=parent.grant_id,
        )
        if grant.expires_at <= int(time.time()):
            raise ValueError("delegated grant must expire in the future")
        self._insert(grant)
        return grant

    def revoke(self, grant_id: str) -> None:
        with self._db() as db:
            changed = db.execute(
                "UPDATE capability_grant SET revoked=1 WHERE grant_id=?",
                (str(grant_id),),
            ).rowcount
        if not changed:
            raise ValueError("unknown grant")

    def get(self, grant_id: str) -> CapabilityGrant | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM capability_grant WHERE grant_id=?",
                (str(grant_id),),
            ).fetchone()
        return self._grant(row) if row else None

    def is_effective(self, grant_id: str, *, now: int | None = None) -> bool:
        checked_at = int(time.time()) if now is None else int(now)
        current = self.get(grant_id)
        seen: set[str] = set()
        while current is not None:
            if current.grant_id in seen or current.revoked or current.expires_at <= checked_at:
                return False
            seen.add(current.grant_id)
            if not current.parent_grant_id:
                return True
            current = self.get(current.parent_grant_id)
        return False

    def allows(
        self,
        grant_id: str,
        *,
        subject: str,
        right: GrantRight,
        object_ref: str,
        now: int | None = None,
    ) -> bool:
        grant = self.get(grant_id)
        return bool(
            grant
            and grant.subject == subject
            and right in grant.rights
            and object_ref in grant.object_refs
            and self.is_effective(grant_id, now=now)
        )

    def _insert(self, grant: CapabilityGrant) -> None:
        with self._db() as db:
            db.execute(
                """INSERT INTO capability_grant(
                    grant_id,issuer,subject,rights_json,object_refs_json,
                    expires_at,parent_grant_id,revoked,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    grant.grant_id,
                    grant.issuer,
                    grant.subject,
                    json.dumps(sorted(right.value for right in grant.rights)),
                    json.dumps(sorted(grant.object_refs)),
                    grant.expires_at,
                    grant.parent_grant_id,
                    1 if grant.revoked else 0,
                    int(time.time()),
                ),
            )

    @staticmethod
    def _grant(row: sqlite3.Row) -> CapabilityGrant:
        return CapabilityGrant(
            grant_id=str(row["grant_id"]),
            issuer=str(row["issuer"]),
            subject=str(row["subject"]),
            rights=frozenset(GrantRight(item) for item in json.loads(row["rights_json"])),
            object_refs=frozenset(str(item) for item in json.loads(row["object_refs_json"])),
            expires_at=int(row["expires_at"]),
            parent_grant_id=str(row["parent_grant_id"]),
            revoked=bool(row["revoked"]),
        )
