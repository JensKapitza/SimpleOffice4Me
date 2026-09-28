"""Best-effort domain activity stream for the additive SimpleOffice 3.0 rollout."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Callable

from .v3_capabilities import enabled as capability_enabled


_CONTROL_DIR = ".simpleoffice-meta"
_SENSITIVE_PARTS = ("password", "passwd", "secret", "token", "credential", "private_key", "document_content", "request_body")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    occurred_at: str
    actor: str
    entity_type: str = ""
    entity_id: str = ""
    correlation_id: str = ""
    source: str = ""
    schema_version: int = 1
    metadata: dict | None = None


def _sanitized_metadata(metadata: dict | None) -> dict:
    value = dict(metadata or {})
    for key in value:
        folded = str(key).casefold()
        if any(part in folded for part in _SENSITIVE_PARTS):
            raise ValueError(f"sensitive event field is not allowed: {key}")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise ValueError("event metadata is too large")
    return value


class ActivityStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / _CONTROL_DIR / "v3-activity.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self):
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS activity_event (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    entity_type TEXT NOT NULL DEFAULT '',
                    entity_id TEXT NOT NULL DEFAULT '',
                    correlation_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    schema_version INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS ix_activity_entity
                    ON activity_event(entity_type, entity_id, occurred_at DESC, event_id DESC);
                CREATE INDEX IF NOT EXISTS ix_activity_type
                    ON activity_event(event_type, occurred_at DESC, event_id DESC);
                CREATE INDEX IF NOT EXISTS ix_activity_actor
                    ON activity_event(actor, occurred_at DESC, event_id DESC);
                """
            )

    def record(
        self,
        event_type: str,
        actor: str,
        *,
        entity_type: str = "",
        entity_id: str = "",
        correlation_id: str = "",
        source: str = "",
        metadata: dict | None = None,
        event_id: str = "",
        occurred_at: str = "",
        schema_version: int = 1,
    ) -> DomainEvent:
        event_type = str(event_type).strip()
        actor = str(actor).strip()
        if not event_type or not actor:
            raise ValueError("event type and actor are required")
        version = int(schema_version)
        if version < 1:
            raise ValueError("event schema version must be positive")
        event = DomainEvent(
            event_id=event_id or uuid.uuid4().hex,
            event_type=event_type[:160],
            occurred_at=occurred_at or _utc(),
            actor=actor[:200],
            entity_type=str(entity_type).strip()[:100],
            entity_id=str(entity_id).strip()[:200],
            correlation_id=str(correlation_id).strip()[:200],
            source=str(source).strip()[:160],
            schema_version=version,
            metadata=_sanitized_metadata(metadata),
        )
        encoded = json.dumps(event.metadata or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._db() as db:
            db.execute(
                """INSERT OR IGNORE INTO activity_event(
                    event_id,event_type,occurred_at,actor,entity_type,entity_id,
                    correlation_id,source,schema_version,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.event_id,event.event_type,event.occurred_at,event.actor,
                    event.entity_type,event.entity_id,event.correlation_id,event.source,
                    event.schema_version,encoded,
                ),
            )
        return event

    def list(
        self,
        *,
        entity_type: str = "",
        entity_id: str = "",
        event_type: str = "",
        actor: str = "",
        since: str = "",
        limit: int = 100,
        before: str = "",
    ) -> list[DomainEvent]:
        size = max(1, min(500, int(limit)))
        params: tuple[object, ...] = (
            entity_type, entity_type,
            entity_id, entity_id,
            event_type, event_type,
            actor, actor,
            since, since,
            before, before,
            size,
        )
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM activity_event
                   WHERE (?='' OR entity_type=?)
                     AND (?='' OR entity_id=?)
                     AND (?='' OR event_type=?)
                     AND (?='' OR actor=?)
                     AND (?='' OR occurred_at>=?)
                     AND (?='' OR event_id<?)
                   ORDER BY occurred_at DESC,event_id DESC LIMIT ?""",
                params,
            ).fetchall()
        return [
            DomainEvent(
                row["event_id"],row["event_type"],row["occurred_at"],row["actor"],
                row["entity_type"],row["entity_id"],row["correlation_id"],row["source"],
                int(row["schema_version"]),json.loads(row["metadata_json"] or "{}"),
            )
            for row in rows
        ]


class EventBus:
    """Optional consumers are isolated; one broken consumer never breaks the domain write."""

    def __init__(self):
        self._consumers: list[Callable[[DomainEvent], None]] = []

    def subscribe(self, consumer: Callable[[DomainEvent], None]) -> None:
        self._consumers.append(consumer)

    def publish(self, event: DomainEvent) -> list[Exception]:
        errors = []
        for consumer in tuple(self._consumers):
            try:
                consumer(event)
            except Exception as exc:
                errors.append(exc)
        return errors


def emit_activity_best_effort(
    root: str | Path,
    event_type: str,
    actor: str,
    *,
    entity_type: str = "",
    entity_id: str = "",
    metadata: dict | None = None,
    correlation_id: str = "",
    source: str = "",
) -> DomainEvent | None:
    """Adapter for existing modules. Disabled/unavailable activity must never fail a domain operation."""
    if not capability_enabled("v3.activity"):
        return None
    try:
        return ActivityStore(root).record(
            event_type, actor,
            entity_type=entity_type, entity_id=entity_id,
            metadata=metadata, correlation_id=correlation_id, source=source,
        )
    except Exception:
        return None
