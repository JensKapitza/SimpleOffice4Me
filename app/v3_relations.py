"""Additive entity references and relation storage for SimpleOffice 3.0."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Callable, Iterable


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, order=True)
class EntityRef:
    type: str
    id: str
    instance: str = ""

    def __post_init__(self):
        for label, value in (("type", self.type), ("id", self.id)):
            if not _TOKEN.fullmatch(str(value)):
                raise ValueError(f"invalid entity {label}")
        if self.instance and not _TOKEN.fullmatch(str(self.instance)):
            raise ValueError("invalid entity instance")

    @property
    def key(self) -> str:
        return f"{self.instance}|{self.type}|{self.id}"


Resolver = Callable[[EntityRef], object | None]
Authorizer = Callable[[str, str, EntityRef], bool]


class EntityRegistry:
    """Resolve existing domain objects without owning or duplicating them."""

    def __init__(self):
        self._resolvers: dict[str, Resolver] = {}
        self._authorizers: dict[str, Authorizer] = {}

    def register(self, entity_type: str, resolver: Resolver, authorizer: Authorizer) -> None:
        if not _TOKEN.fullmatch(entity_type):
            raise ValueError("invalid entity type")
        if entity_type in self._resolvers:
            raise ValueError(f"entity type {entity_type!r} is already registered")
        self._resolvers[entity_type] = resolver
        self._authorizers[entity_type] = authorizer

    def known(self, ref: EntityRef) -> bool:
        return ref.type in self._resolvers

    def resolve(self, ref: EntityRef):
        resolver = self._resolvers.get(ref.type)
        return resolver(ref) if resolver is not None else None

    def exists(self, ref: EntityRef) -> bool:
        return self.resolve(ref) is not None

    def can(self, principal: str, action: str, ref: EntityRef) -> bool:
        authorizer = self._authorizers.get(ref.type)
        if authorizer is None:
            return False
        try:
            return bool(authorizer(principal, action, ref))
        except Exception:
            return False


@dataclass(frozen=True)
class RelationType:
    name: str
    directed: bool = False
    allow_cycles: bool = True


@dataclass(frozen=True)
class Relation:
    relation_id: str
    relation_type: str
    source: EntityRef
    target: EntityRef
    created_by: str
    created_at: str
    metadata: dict
    source_exists: bool = True
    target_exists: bool = True


class RelationStore:
    def __init__(self, database: str | Path, registry: EntityRegistry):
        self.database = Path(database)
        self.registry = registry
        self._types: dict[str, RelationType] = {}
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _db(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self):
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS v3_relation (
                    relation_id TEXT PRIMARY KEY,
                    relation_type TEXT NOT NULL,
                    source_instance TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_instance TEXT NOT NULL DEFAULT '',
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_v3_relation_identity
                ON v3_relation(
                    relation_type,
                    source_instance, source_type, source_id,
                    target_instance, target_type, target_id
                );
                CREATE INDEX IF NOT EXISTS ix_v3_relation_source
                ON v3_relation(source_instance, source_type, source_id, relation_type, created_at, relation_id);
                CREATE INDEX IF NOT EXISTS ix_v3_relation_target
                ON v3_relation(target_instance, target_type, target_id, relation_type, created_at, relation_id);
                """
            )

    def register_type(self, relation_type: RelationType) -> None:
        if not _TOKEN.fullmatch(relation_type.name):
            raise ValueError("invalid relation type")
        existing = self._types.get(relation_type.name)
        if existing is not None and existing != relation_type:
            raise ValueError("relation type already registered differently")
        self._types[relation_type.name] = relation_type

    def _type(self, name: str) -> RelationType:
        result = self._types.get(name)
        if result is None:
            raise ValueError(f"unknown relation type {name!r}")
        return result

    @staticmethod
    def _canonical(kind: RelationType, source: EntityRef, target: EntityRef) -> tuple[EntityRef, EntityRef]:
        if not kind.directed and target.key < source.key:
            return target, source
        return source, target

    def _require_link_permission(self, principal: str, source: EntityRef, target: EntityRef) -> None:
        if not self.registry.known(source) or not self.registry.known(target):
            raise ValueError("unknown entity type")
        if not self.registry.exists(source) or not self.registry.exists(target):
            raise LookupError("entity does not exist")
        if not self.registry.can(principal, "link", source) or not self.registry.can(principal, "link", target):
            raise PermissionError("relation permission denied")

    def _would_cycle(self, kind: RelationType, source: EntityRef, target: EntityRef) -> bool:
        if not kind.directed or kind.allow_cycles:
            return False
        if source == target:
            return True
        frontier = [target]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current.key in visited:
                continue
            visited.add(current.key)
            if current == source:
                return True
            with self._db() as db:
                rows = db.execute(
                    """SELECT target_instance,target_type,target_id FROM v3_relation
                       WHERE relation_type=? AND source_instance=? AND source_type=? AND source_id=? LIMIT 1000""",
                    (kind.name, current.instance, current.type, current.id),
                ).fetchall()
            frontier.extend(EntityRef(row["target_type"], row["target_id"], row["target_instance"]) for row in rows)
            if len(visited) > 10000:
                raise RuntimeError("relation cycle check exceeded safety bound")
        return False

    def add(
        self,
        principal: str,
        relation_type: str,
        source: EntityRef,
        target: EntityRef,
        *,
        metadata: dict | None = None,
    ) -> Relation:
        kind = self._type(relation_type)
        source, target = self._canonical(kind, source, target)
        self._require_link_permission(principal, source, target)
        if self._would_cycle(kind, source, target):
            raise ValueError("relation would create a forbidden cycle")
        payload = metadata or {}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("relation metadata is too large")
        relation_id = uuid.uuid4().hex
        created_at = _utc()
        try:
            with self._db() as db:
                db.execute(
                    """INSERT INTO v3_relation(
                        relation_id,relation_type,
                        source_instance,source_type,source_id,
                        target_instance,target_type,target_id,
                        created_by,created_at,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        relation_id, kind.name,
                        source.instance, source.type, source.id,
                        target.instance, target.type, target.id,
                        principal, created_at, encoded,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("relation already exists") from exc
        return Relation(relation_id, kind.name, source, target, principal, created_at, payload)

    def _row(self, row: sqlite3.Row) -> Relation:
        source = EntityRef(row["source_type"], row["source_id"], row["source_instance"])
        target = EntityRef(row["target_type"], row["target_id"], row["target_instance"])
        return Relation(
            row["relation_id"], row["relation_type"], source, target,
            row["created_by"], row["created_at"], json.loads(row["metadata_json"] or "{}"),
            self.registry.exists(source), self.registry.exists(target),
        )

    def get(self, principal: str, relation_id: str) -> Relation | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM v3_relation WHERE relation_id=?", (relation_id,)).fetchone()
        if row is None:
            return None
        relation = self._row(row)
        if not self.registry.can(principal, "read", relation.source):
            return None
        if not self.registry.can(principal, "read", relation.target):
            return None
        return relation

    def list_for(
        self,
        principal: str,
        entity: EntityRef,
        *,
        relation_type: str | None = None,
        limit: int = 100,
        after: str = "",
    ) -> list[Relation]:
        if not self.registry.can(principal, "read", entity):
            return []
        size = max(1, min(500, int(limit)))
        if relation_type:
            self._type(relation_type)
        params: tuple[object, ...] = (
            entity.instance, entity.type, entity.id,
            entity.instance, entity.type, entity.id,
            relation_type or "", relation_type or "",
            after or "", after or "",
            size * 4,
        )
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM v3_relation
                   WHERE ((source_instance=? AND source_type=? AND source_id=?)
                       OR (target_instance=? AND target_type=? AND target_id=?))
                     AND (?='' OR relation_type=?)
                     AND (?='' OR relation_id>?)
                   ORDER BY relation_id LIMIT ?""",
                params,
            ).fetchall()
        result: list[Relation] = []
        for row in rows:
            relation = self._row(row)
            other = relation.target if relation.source == entity else relation.source
            if self.registry.can(principal, "read", other):
                result.append(relation)
                if len(result) >= size:
                    break
        return result

    def update_metadata(self, principal: str, relation_id: str, metadata: dict) -> Relation:
        relation = self.get(principal, relation_id)
        if relation is None:
            raise LookupError("unknown relation")
        if not self.registry.can(principal, "link", relation.source):
            raise PermissionError("relation permission denied")
        if not self.registry.can(principal, "link", relation.target):
            raise PermissionError("relation permission denied")
        payload = dict(metadata or {})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("relation metadata is too large")
        with self._db() as db:
            db.execute(
                "UPDATE v3_relation SET metadata_json=? WHERE relation_id=?",
                (encoded, relation_id),
            )
        updated = self.get(principal, relation_id)
        if updated is None:
            raise LookupError("relation disappeared")
        return updated

    def remove(self, principal: str, relation_id: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT * FROM v3_relation WHERE relation_id=?", (relation_id,)).fetchone()
            if row is None:
                return False
            relation = self._row(row)
            if not self.registry.can(principal, "link", relation.source):
                raise PermissionError("relation permission denied")
            if not self.registry.can(principal, "link", relation.target):
                raise PermissionError("relation permission denied")
            db.execute("DELETE FROM v3_relation WHERE relation_id=?", (relation_id,))
        return True


def default_relation_types() -> Iterable[RelationType]:
    return (
        RelationType("related_to", directed=False),
        RelationType("belongs_to", directed=True, allow_cycles=False),
        RelationType("contact_for", directed=True, allow_cycles=False),
        RelationType("document_for", directed=True, allow_cycles=False),
        RelationType("parent_of", directed=True, allow_cycles=False),
    )
