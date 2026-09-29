"""Controlled Android offline cache and outbox for SimpleOffice 3.0.

The cache is explicitly selected, user-scoped and bounded. It never mutates
authoritative domain stores. A transport may refresh snapshots and submit the
outbox to an authoritative server.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any, Iterable, Mapping

from .sqlite_utils import connect as sqlite_connect


OFFLINE_CAPABILITY = "v3.android_offline"
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
MAX_ITEMS = 500
MAX_RETENTION = 30 * 24 * 60 * 60
DEFAULT_RETENTION = 7 * 24 * 60 * 60
OFFLINE_TYPES = frozenset({"document", "project", "task", "calendar_event"})
PAYLOAD_FIELDS = {
    "document": frozenset({"title", "mime", "size", "summary", "updated_at"}),
    "project": frozenset({"name", "status", "summary", "updated_at"}),
    "task": frozenset({"title", "status", "start", "due", "priority", "updated_at"}),
    "calendar_event": frozenset({"title", "start", "end", "location", "updated_at"}),
}


def policy() -> dict[str, str]:
    result = {kind: "offline_allowed" for kind in sorted(OFFLINE_TYPES)}
    result.update({"contact": "online_only", "finance": "online_only", "mail": "online_only"})
    return result


def _id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN.fullmatch(text):
        raise ValueError(f"invalid {label}")
    return text


def _owner(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 320 or any(ord(ch) < 32 for ch in text):
        raise ValueError("invalid principal")
    return text


def _payload(entity_type: str, value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = PAYLOAD_FIELDS.get(entity_type)
    keys = set(map(str, value.keys()))
    if not allowed or keys - allowed:
        raise ValueError("offline payload contains unsupported fields")
    clean = dict(value)
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ValueError("offline payload is too large")
    return clean


class OfflineCacheStore:
    """SQLite-backed per-user cache with idempotent task-status outbox."""

    def __init__(self, root: str | Path):
        self.path = Path(root).expanduser().resolve() / ".simpleoffice-meta" / "v3-android-offline.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _db(self):
        db = sqlite_connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS android_offline_workset(
                    principal TEXT NOT NULL,
                    workset_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    selection_json TEXT NOT NULL,
                    retention_seconds INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(principal,workset_id)
                );
                CREATE TABLE IF NOT EXISTS android_offline_item(
                    principal TEXT NOT NULL,
                    workset_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    server_version TEXT NOT NULL,
                    etag TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    synced_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY(principal,workset_id,entity_type,entity_id),
                    FOREIGN KEY(principal,workset_id)
                      REFERENCES android_offline_workset(principal,workset_id)
                      ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ix_android_offline_item_expiry
                  ON android_offline_item(expires_at,principal);
                CREATE TABLE IF NOT EXISTS android_offline_outbox(
                    operation_id TEXT PRIMARY KEY,
                    principal TEXT NOT NULL,
                    workset_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    base_version TEXT NOT NULL,
                    status_value TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    server_version TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(principal,workset_id)
                      REFERENCES android_offline_workset(principal,workset_id)
                      ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ix_android_offline_outbox_pending
                  ON android_offline_outbox(principal,state,created_at);
                """
            )

    def save_workset(
        self,
        principal: str,
        workset_id: str,
        server_id: str,
        selection: Iterable[Mapping[str, Any]],
        *,
        label: str = "",
        retention_seconds: int = DEFAULT_RETENTION,
        now: int | None = None,
    ) -> dict[str, Any]:
        owner = _owner(principal)
        key = _id(workset_id, "workset id")
        server = _id(server_id, "server id")
        refs: list[dict[str, str]] = []
        for row in selection:
            if len(refs) >= MAX_ITEMS:
                raise ValueError("offline workset is too large")
            kind = _id(row.get("type"), "entity type")
            entity = _id(row.get("id"), "entity id")
            if kind not in OFFLINE_TYPES:
                raise ValueError("entity class is not available offline")
            ref = {"type": kind, "id": entity}
            if ref not in refs:
                refs.append(ref)
        if not refs:
            raise ValueError("offline workset must select at least one entity")
        retention = max(60, min(MAX_RETENTION, int(retention_seconds)))
        current = int(time.time()) if now is None else int(now)
        encoded = json.dumps(refs, sort_keys=True, separators=(",", ":"))
        with self._db() as db:
            db.execute(
                """INSERT INTO android_offline_workset(
                       principal,workset_id,server_id,label,selection_json,retention_seconds,updated_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(principal,workset_id) DO UPDATE SET
                       server_id=excluded.server_id,label=excluded.label,
                       selection_json=excluded.selection_json,
                       retention_seconds=excluded.retention_seconds,
                       updated_at=excluded.updated_at""",
                (owner, key, server, " ".join(str(label).split())[:160], encoded, retention, current),
            )
            selected = {(row["type"], row["id"]) for row in refs}
            cached = db.execute(
                "SELECT entity_type,entity_id FROM android_offline_item WHERE principal=? AND workset_id=?",
                (owner, key),
            ).fetchall()
            for row in cached:
                ref = (str(row["entity_type"]), str(row["entity_id"]))
                if ref not in selected:
                    db.execute(
                        """DELETE FROM android_offline_item
                           WHERE principal=? AND workset_id=? AND entity_type=? AND entity_id=?""",
                        (owner, key, *ref),
                    )
        return {
            "workset_id": key,
            "server_id": server,
            "selection": refs,
            "retention_seconds": retention,
        }

    def _workset(self, principal: str, workset_id: str) -> sqlite3.Row:
        owner = _owner(principal)
        key = _id(workset_id, "workset id")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM android_offline_workset WHERE principal=? AND workset_id=?",
                (owner, key),
            ).fetchone()
        if row is None:
            raise LookupError("offline workset does not exist")
        return row

    def cache_item(
        self,
        principal: str,
        workset_id: str,
        entity_type: str,
        entity_id: str,
        *,
        server_version: str,
        etag: str = "",
        payload: Mapping[str, Any],
        now: int | None = None,
    ) -> dict[str, Any]:
        owner = _owner(principal)
        key = _id(workset_id, "workset id")
        kind = _id(entity_type, "entity type")
        entity = _id(entity_id, "entity id")
        workset = self._workset(owner, key)
        selected = {(row["type"], row["id"]) for row in json.loads(workset["selection_json"])}
        if (kind, entity) not in selected:
            raise PermissionError("entity is not selected for this workset")
        clean = _payload(kind, payload)
        version = str(server_version or "").strip()[:160]
        tag = str(etag or "").strip()[:240]
        if not version and not tag:
            raise ValueError("server version or etag is required")
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        current = int(time.time()) if now is None else int(now)
        expires = current + int(workset["retention_seconds"])
        with self._db() as db:
            db.execute(
                """INSERT INTO android_offline_item(
                       principal,workset_id,entity_type,entity_id,server_version,etag,
                       payload_json,synced_at,expires_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(principal,workset_id,entity_type,entity_id) DO UPDATE SET
                       server_version=excluded.server_version,etag=excluded.etag,
                       payload_json=excluded.payload_json,synced_at=excluded.synced_at,
                       expires_at=excluded.expires_at""",
                (owner, key, kind, entity, version, tag, encoded, current, expires),
            )
        return {
            "type": kind,
            "id": entity,
            "server_version": version,
            "etag": tag,
            "payload": clean,
            "expires_at": expires,
        }

    def items(self, principal: str, workset_id: str, *, now: int | None = None) -> list[dict[str, Any]]:
        owner = _owner(principal)
        key = _id(workset_id, "workset id")
        current = int(time.time()) if now is None else int(now)
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM android_offline_item
                   WHERE principal=? AND workset_id=? AND expires_at>=?
                   ORDER BY entity_type,entity_id LIMIT ?""",
                (owner, key, current, MAX_ITEMS),
            ).fetchall()
        return [
            {
                "type": str(row["entity_type"]),
                "id": str(row["entity_id"]),
                "server_version": str(row["server_version"]),
                "etag": str(row["etag"]),
                "payload": json.loads(row["payload_json"]),
                "expires_at": int(row["expires_at"]),
            }
            for row in rows
        ]

    def queue_task_status(
        self,
        principal: str,
        workset_id: str,
        task_id: str,
        status_value: str,
        base_version: str,
        *,
        operation_id: str = "",
        now: int | None = None,
    ) -> dict[str, Any]:
        owner = _owner(principal)
        key = _id(workset_id, "workset id")
        task = _id(task_id, "task id")
        operation = _id(operation_id or uuid.uuid4().hex, "operation id")
        status = " ".join(str(status_value or "").split())[:80]
        base = str(base_version or "").strip()[:160]
        if not status or not base:
            raise ValueError("status and base version are required")
        workset = self._workset(owner, key)
        selected = {(row["type"], row["id"]) for row in json.loads(workset["selection_json"])}
        if ("task", task) not in selected:
            raise PermissionError("task is not selected for this workset")
        digest = hashlib.sha256(
            f"{owner}\0{key}\0{task}\0{base}\0{status}".encode("utf-8")
        ).hexdigest()
        current = int(time.time()) if now is None else int(now)
        with self._db() as db:
            existing = db.execute(
                "SELECT * FROM android_offline_outbox WHERE operation_id=?",
                (operation,),
            ).fetchone()
            if existing:
                if str(existing["request_digest"]) != digest:
                    raise ValueError("operation id replay has different content")
                return dict(existing)
            db.execute(
                """INSERT INTO android_offline_outbox(
                       operation_id,principal,workset_id,entity_id,base_version,status_value,
                       request_digest,state,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
                (operation, owner, key, task, base, status, digest, current, current),
            )
            row = db.execute(
                "SELECT * FROM android_offline_outbox WHERE operation_id=?",
                (operation,),
            ).fetchone()
        return dict(row)

    def pending(self, principal: str) -> list[dict[str, Any]]:
        owner = _owner(principal)
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM android_offline_outbox
                   WHERE principal=? AND state IN ('pending','syncing','conflict')
                   ORDER BY created_at,operation_id LIMIT 500""",
                (owner,),
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve(
        self,
        principal: str,
        operation_id: str,
        state: str,
        *,
        server_version: str = "",
        error_code: str = "",
        now: int | None = None,
    ) -> dict[str, Any]:
        owner = _owner(principal)
        operation = _id(operation_id, "operation id")
        state = str(state or "").strip().casefold()
        if state not in {"pending", "syncing", "applied", "conflict", "failed"}:
            raise ValueError("invalid outbox state")
        current = int(time.time()) if now is None else int(now)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM android_offline_outbox WHERE operation_id=? AND principal=?",
                (operation, owner),
            ).fetchone()
            if row is None:
                raise LookupError("offline operation does not exist")
            if str(row["state"]) == "applied" and state != "applied":
                raise ValueError("applied operation is terminal")
            db.execute(
                """UPDATE android_offline_outbox
                   SET state=?,server_version=?,error_code=?,updated_at=?
                   WHERE operation_id=? AND principal=?""",
                (
                    state,
                    str(server_version or "").strip()[:160],
                    str(error_code or "").strip()[:120],
                    current,
                    operation,
                    owner,
                ),
            )
            updated = db.execute(
                "SELECT * FROM android_offline_outbox WHERE operation_id=? AND principal=?",
                (operation, owner),
            ).fetchone()
        return dict(updated)

    def purge_principal(self, principal: str) -> int:
        owner = _owner(principal)
        with self._db() as db:
            count = int(db.execute(
                "SELECT COUNT(*) FROM android_offline_workset WHERE principal=?",
                (owner,),
            ).fetchone()[0])
            db.execute("DELETE FROM android_offline_workset WHERE principal=?", (owner,))
        return count

    def purge_except(self, principal: str) -> int:
        owner = _owner(principal)
        with self._db() as db:
            count = int(db.execute(
                "SELECT COUNT(*) FROM android_offline_workset WHERE principal<>?",
                (owner,),
            ).fetchone()[0])
            db.execute("DELETE FROM android_offline_workset WHERE principal<>?", (owner,))
        return count

    def prune_expired(self, *, now: int | None = None) -> int:
        current = int(time.time()) if now is None else int(now)
        with self._db() as db:
            count = int(db.execute(
                "SELECT COUNT(*) FROM android_offline_item WHERE expires_at<?",
                (current,),
            ).fetchone()[0])
            db.execute("DELETE FROM android_offline_item WHERE expires_at<?", (current,))
        return count
