"""Offline remote-file catalog and dynamic download queue for SOFP."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR
from .federation_core import normalize_sha256, sanitize_peer_id


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, json.JSONDecodeError):
        return default


def _priority(value: Any) -> int:
    try:
        return max(-1000, min(1000, int(value)))
    except (TypeError, ValueError):
        return 0


class FederationCatalog:
    """Persist remote indexes even while peers are unavailable.

    Effective queue priority is intentionally calculated at read time from three
    independently mutable values: server + file + transfer. This means changing a
    server or file priority immediately reorders all pending transfers without
    rewriting every queue row.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "federation-catalog.sqlite3"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS federation_catalog_peer(
                    peer_id TEXT PRIMARY KEY,
                    server_priority INTEGER NOT NULL DEFAULT 0,
                    generation TEXT NOT NULL DEFAULT '',
                    last_index_at INTEGER,
                    last_index_error TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS federation_remote_file(
                    peer_id TEXT NOT NULL,
                    remote_document_id TEXT NOT NULL,
                    blob_hash TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0,
                    modified_at TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    origin_tags_json TEXT NOT NULL DEFAULT '[]',
                    file_priority INTEGER NOT NULL DEFAULT 0,
                    available INTEGER NOT NULL DEFAULT 1,
                    generation TEXT NOT NULL DEFAULT '',
                    indexed_at INTEGER NOT NULL,
                    PRIMARY KEY(peer_id, remote_document_id)
                );
                CREATE INDEX IF NOT EXISTS federation_remote_file_hash
                    ON federation_remote_file(peer_id, blob_hash);
                CREATE INDEX IF NOT EXISTS federation_remote_file_path
                    ON federation_remote_file(peer_id, path COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS federation_download_request(
                    request_id TEXT PRIMARY KEY,
                    peer_id TEXT NOT NULL,
                    remote_document_id TEXT NOT NULL,
                    blob_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    transfer_priority INTEGER NOT NULL DEFAULT 0,
                    requested_by TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    local_document_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS federation_download_queue
                    ON federation_download_request(status, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS federation_download_remote
                    ON federation_download_request(peer_id, remote_document_id, status);
                CREATE TABLE IF NOT EXISTS federation_download_event(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL DEFAULT '',
                    peer_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS federation_download_event_created
                    ON federation_download_event(created_at DESC);
                """
            )

    def _ensure_peer(self, peer_id: str) -> str:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO federation_catalog_peer(peer_id,updated_at) VALUES(?,?)",
                (peer_id, _now()),
            )
        return peer_id

    def set_server_priority(self, peer_id: str, priority: int) -> None:
        peer_id = self._ensure_peer(peer_id)
        value = _priority(priority)
        with self._db() as db:
            db.execute(
                "UPDATE federation_catalog_peer SET server_priority=?,updated_at=? WHERE peer_id=?",
                (value, _now(), peer_id),
            )
        self.record_event("server_priority_changed", peer_id=peer_id, detail={"priority": value})

    def peer_state(self, peer_id: str) -> dict[str, Any]:
        peer_id = self._ensure_peer(peer_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM federation_catalog_peer WHERE peer_id=?", (peer_id,)).fetchone()
        return dict(row) if row else {}

    def begin_index(self, peer_id: str, generation: str) -> None:
        peer_id = self._ensure_peer(peer_id)
        with self._db() as db:
            db.execute(
                "UPDATE federation_catalog_peer SET generation=?,last_index_error='',updated_at=? WHERE peer_id=?",
                (generation[:160], _now(), peer_id),
            )
        self.record_event("index_sync_started", peer_id=peer_id, detail={"generation": generation})

    def ingest(self, peer_id: str, generation: str, rows: list[dict[str, Any]]) -> int:
        peer_id = self._ensure_peer(peer_id)
        timestamp = _now()
        inserted = 0
        with self._db() as db:
            for item in rows:
                remote_document_id = str(item.get("document_id") or "").strip()[:200]
                if not remote_document_id:
                    continue
                try:
                    digest = normalize_sha256(item.get("blob_hash", ""))
                    size = max(0, int(item.get("size", 0)))
                except (TypeError, ValueError):
                    continue
                path = str(item.get("path") or "")[:4000]
                tags = sorted({str(tag)[:200] for tag in item.get("tags", []) if str(tag).strip()}, key=str.casefold)
                origin_tags = sorted({str(tag)[:200] for tag in item.get("origin_tags", []) if str(tag).strip()}, key=str.casefold)
                current = db.execute(
                    "SELECT file_priority FROM federation_remote_file WHERE peer_id=? AND remote_document_id=?",
                    (peer_id, remote_document_id),
                ).fetchone()
                file_priority = int(current[0]) if current else 0
                db.execute(
                    """INSERT INTO federation_remote_file(
                           peer_id,remote_document_id,blob_hash,path,size,modified_at,tags_json,
                           origin_tags_json,file_priority,available,generation,indexed_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(peer_id,remote_document_id) DO UPDATE SET
                         blob_hash=excluded.blob_hash,path=excluded.path,size=excluded.size,
                         modified_at=excluded.modified_at,tags_json=excluded.tags_json,
                         origin_tags_json=excluded.origin_tags_json,available=1,
                         generation=excluded.generation,indexed_at=excluded.indexed_at""",
                    (
                        peer_id, remote_document_id, digest, path, size,
                        str(item.get("modified_at") or "")[:160], _json(tags), _json(origin_tags),
                        file_priority, 1, generation[:160], timestamp,
                    ),
                )
                inserted += 1
        return inserted

    def finish_index(self, peer_id: str, generation: str) -> None:
        peer_id = self._ensure_peer(peer_id)
        timestamp = _now()
        with self._db() as db:
            db.execute(
                "UPDATE federation_remote_file SET available=0 WHERE peer_id=? AND generation<>?",
                (peer_id, generation[:160]),
            )
            db.execute(
                "UPDATE federation_catalog_peer SET generation=?,last_index_at=?,last_index_error='',updated_at=? WHERE peer_id=?",
                (generation[:160], timestamp, timestamp, peer_id),
            )
        self.record_event("index_sync_completed", peer_id=peer_id, detail={"generation": generation})

    def fail_index(self, peer_id: str, error: str) -> None:
        peer_id = self._ensure_peer(peer_id)
        with self._db() as db:
            db.execute(
                "UPDATE federation_catalog_peer SET last_index_error=?,updated_at=? WHERE peer_id=?",
                (str(error)[:1000], _now(), peer_id),
            )
        self.record_event("index_sync_failed", peer_id=peer_id, detail={"error": str(error)[:500]})

    def remote_files(self, peer_id: str = "", query: str = "", limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        clauses, values = [], []
        if peer_id:
            clauses.append("f.peer_id=?")
            values.append(sanitize_peer_id(peer_id))
        if query:
            clauses.append("(f.path LIKE ? OR f.remote_document_id LIKE ? OR f.tags_json LIKE ?)")
            needle = f"%{query[:200]}%"
            values.extend([needle, needle, needle])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._db() as db:
            rows = db.execute(
                """SELECT f.*,COALESCE(p.server_priority,0) AS server_priority
                   FROM federation_remote_file f
                   LEFT JOIN federation_catalog_peer p ON p.peer_id=f.peer_id"""
                + where + " ORDER BY f.available DESC,f.path COLLATE NOCASE LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [self._remote(row) for row in rows]

    def get_remote(self, peer_id: str, remote_document_id: str) -> dict[str, Any] | None:
        peer_id = sanitize_peer_id(peer_id)
        with self._db() as db:
            row = db.execute(
                """SELECT f.*,COALESCE(p.server_priority,0) AS server_priority
                   FROM federation_remote_file f LEFT JOIN federation_catalog_peer p ON p.peer_id=f.peer_id
                   WHERE f.peer_id=? AND f.remote_document_id=?""",
                (peer_id, remote_document_id),
            ).fetchone()
        return self._remote(row) if row else None

    def set_file_priority(self, peer_id: str, remote_document_id: str, priority: int) -> None:
        peer_id = sanitize_peer_id(peer_id)
        value = _priority(priority)
        with self._db() as db:
            cursor = db.execute(
                "UPDATE federation_remote_file SET file_priority=? WHERE peer_id=? AND remote_document_id=?",
                (value, peer_id, remote_document_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Remote-Datei ist nicht im Offline-Index")
        self.record_event(
            "file_priority_changed", peer_id=peer_id,
            detail={"remote_document_id": remote_document_id, "priority": value},
        )

    def request_download(
        self,
        peer_id: str,
        remote_document_id: str,
        *,
        requested_by: str,
        transfer_priority: int = 0,
    ) -> dict[str, Any]:
        remote = self.get_remote(peer_id, remote_document_id)
        if not remote:
            raise ValueError("Remote-Datei ist nicht im Offline-Index")
        timestamp = _now()
        request_id = str(uuid.uuid4())
        with self._db() as db:
            existing = db.execute(
                """SELECT request_id FROM federation_download_request
                   WHERE peer_id=? AND remote_document_id=? AND status IN ('queued','waiting_peer','running','retry')
                   ORDER BY created_at DESC LIMIT 1""",
                (remote["peer_id"], remote_document_id),
            ).fetchone()
            if existing:
                return self.get_request(str(existing[0])) or {}
            db.execute(
                """INSERT INTO federation_download_request(
                       request_id,peer_id,remote_document_id,blob_hash,status,transfer_priority,
                       requested_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    request_id, remote["peer_id"], remote_document_id, remote["blob_hash"],
                    "queued", _priority(transfer_priority), str(requested_by)[:160], timestamp, timestamp,
                ),
            )
        self.record_event(
            "download_requested", request_id=request_id, peer_id=remote["peer_id"],
            detail={"remote_document_id": remote_document_id, "transfer_priority": _priority(transfer_priority)},
        )
        return self.get_request(request_id) or {}

    def set_request_priority(self, request_id: str, priority: int) -> dict[str, Any]:
        value = _priority(priority)
        with self._db() as db:
            cursor = db.execute(
                "UPDATE federation_download_request SET transfer_priority=?,updated_at=? WHERE request_id=?",
                (value, _now(), request_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Download-Anforderung ist unbekannt")
        result = self.get_request(request_id) or {}
        self.record_event(
            "transfer_priority_changed", request_id=request_id, peer_id=result.get("peer_id", ""),
            detail={"priority": value},
        )
        return result

    def update_request(self, request_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"status", "attempts", "next_attempt_at", "last_error", "local_document_id"}
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return self.get_request(request_id) or {}
        updates["updated_at"] = _now()
        with self._db() as db:
            cursor = db.execute(
                "UPDATE federation_download_request SET " + ",".join(f"{key}=?" for key in updates) + " WHERE request_id=?",
                (*updates.values(), request_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Download-Anforderung ist unbekannt")
        return self.get_request(request_id) or {}

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(self._queue_sql(" WHERE q.request_id=?"), (request_id,)).fetchone()
        return self._request(row) if row else None

    def queue(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                self._queue_sql("") +
                " ORDER BY CASE q.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'retry' THEN 2 WHEN 'waiting_peer' THEN 3 ELSE 4 END, effective_priority DESC,q.created_at ASC LIMIT ?",
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        return [self._request(row) for row in rows]

    def next_requests(self, limit: int = 1) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                self._queue_sql(
                    " WHERE q.status IN ('queued','retry','waiting_peer') AND q.next_attempt_at<=?"
                ) + " ORDER BY effective_priority DESC,q.created_at ASC LIMIT ?",
                (_now(), max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._request(row) for row in rows]

    def record_event(
        self,
        action: str,
        *,
        request_id: str = "",
        peer_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO federation_download_event(request_id,peer_id,action,detail_json,created_at) VALUES(?,?,?,?,?)",
                (request_id, peer_id, str(action)[:160], _json(detail or {}), _now()),
            )

    def events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM federation_download_event ORDER BY event_id DESC LIMIT ?",
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"], "request_id": row["request_id"], "peer_id": row["peer_id"],
                "action": row["action"], "detail": _load(row["detail_json"], {}), "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _queue_sql(where: str) -> str:
        return (
            "SELECT q.*,f.path,f.size,f.tags_json,f.origin_tags_json,f.file_priority,f.available,"
            "COALESCE(p.server_priority,0) AS server_priority,"
            "(COALESCE(p.server_priority,0)+COALESCE(f.file_priority,0)+q.transfer_priority) AS effective_priority "
            "FROM federation_download_request q "
            "JOIN federation_remote_file f ON f.peer_id=q.peer_id AND f.remote_document_id=q.remote_document_id "
            "LEFT JOIN federation_catalog_peer p ON p.peer_id=q.peer_id" + where
        )

    @staticmethod
    def _remote(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "peer_id": row["peer_id"], "remote_document_id": row["remote_document_id"],
            "blob_hash": row["blob_hash"], "path": row["path"], "size": row["size"],
            "modified_at": row["modified_at"], "tags": _load(row["tags_json"], []),
            "origin_tags": _load(row["origin_tags_json"], []), "file_priority": row["file_priority"],
            "server_priority": row["server_priority"], "available": bool(row["available"]),
            "generation": row["generation"], "indexed_at": row["indexed_at"],
        }

    @staticmethod
    def _request(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "request_id": row["request_id"], "peer_id": row["peer_id"],
            "remote_document_id": row["remote_document_id"], "blob_hash": row["blob_hash"],
            "status": row["status"], "server_priority": row["server_priority"],
            "file_priority": row["file_priority"], "transfer_priority": row["transfer_priority"],
            "effective_priority": row["effective_priority"], "path": row["path"], "size": row["size"],
            "tags": _load(row["tags_json"], []), "origin_tags": _load(row["origin_tags_json"], []),
            "available": bool(row["available"]), "requested_by": row["requested_by"],
            "attempts": row["attempts"], "next_attempt_at": row["next_attempt_at"],
            "last_error": row["last_error"], "local_document_id": row["local_document_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
