"""Private-by-default, expiring chat status storage."""
from __future__ import annotations

import sqlite3
from .sqlite_utils import connect as sqlite_connect
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .document_store import CONTROL_DIR
from .revision_history import RevisionHistory

MAX_STATUS_TEXT = 2000
DEFAULT_TTL_SECONDS = 24 * 60 * 60
MAX_TTL_SECONDS = 24 * 60 * 60


class ChatStatusStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "chat-status.sqlite3"
        self.history = RevisionHistory(self.root)
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite_connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_status(
                    status_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    deleted_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS chat_status_viewer(
                    status_id TEXT NOT NULL,
                    viewer TEXT NOT NULL,
                    PRIMARY KEY(status_id,viewer),
                    FOREIGN KEY(status_id) REFERENCES chat_status(status_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS chat_status_owner_idx
                    ON chat_status(owner,expires_at,deleted_at);
                CREATE INDEX IF NOT EXISTS chat_status_viewer_idx
                    ON chat_status_viewer(viewer,status_id);
                """
            )

    @staticmethod
    def _principal(value: str) -> str:
        value = str(value or "").strip()
        if not value or len(value) > 120 or any(ord(char) < 32 for char in value):
            raise ValueError("invalid status principal")
        return value

    def publish(
        self,
        owner: str,
        body: str,
        *,
        viewers: list[str],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: int | None = None,
    ) -> dict:
        owner = self._principal(owner)
        text = str(body or "").strip()
        if not text or len(text) > MAX_STATUS_TEXT:
            raise ValueError("invalid status text")
        allowed = sorted({self._principal(item) for item in viewers if str(item or "").strip()} - {owner})
        if not allowed:
            raise ValueError("status requires at least one explicit viewer")
        ttl = int(ttl_seconds)
        if ttl < 1 or ttl > MAX_TTL_SECONDS:
            raise ValueError("invalid status lifetime")
        created_at = int(time.time() if now is None else now)
        row = {
            "status_id": str(uuid.uuid4()),
            "owner": owner,
            "body": text,
            "created_at": created_at,
            "expires_at": created_at + ttl,
            "deleted_at": None,
        }
        with self._db() as db:
            db.execute(
                """INSERT INTO chat_status(status_id,owner,body,created_at,expires_at,deleted_at)
                   VALUES(?,?,?,?,?,NULL)""",
                (row["status_id"], owner, text, row["created_at"], row["expires_at"]),
            )
            db.executemany(
                "INSERT INTO chat_status_viewer(status_id,viewer) VALUES(?,?)",
                [(row["status_id"], viewer) for viewer in allowed],
            )
        self.history.record(
            "chat_status_published",
            owner,
            "chat-status",
            row["status_id"],
            {
                "status_id": row["status_id"],
                "owner": owner,
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "viewer_count": len(allowed),
            },
        )
        return {**row, "viewers": allowed}

    def visible_for(self, viewer: str, *, now: int | None = None) -> list[dict]:
        viewer = self._principal(viewer)
        timestamp = int(time.time() if now is None else now)
        with self._db() as db:
            rows = db.execute(
                """SELECT s.*
                   FROM chat_status s
                   JOIN chat_status_viewer v ON v.status_id=s.status_id
                   WHERE v.viewer=? AND s.deleted_at IS NULL AND s.expires_at>?
                   ORDER BY s.created_at DESC,s.status_id DESC""",
                (viewer, timestamp),
            ).fetchall()
        return [dict(row) for row in rows]

    def own(self, owner: str, *, include_expired: bool = False, now: int | None = None) -> list[dict]:
        owner = self._principal(owner)
        timestamp = int(time.time() if now is None else now)
        with self._db() as db:
            if include_expired:
                rows = db.execute(
                    "SELECT * FROM chat_status WHERE owner=? AND deleted_at IS NULL ORDER BY created_at DESC",
                    (owner,),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM chat_status
                       WHERE owner=? AND deleted_at IS NULL AND expires_at>?
                       ORDER BY created_at DESC""",
                    (owner, timestamp),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_for_actor(self, status_id: str, actor: str, *, now: int | None = None) -> dict:
        actor = self._principal(actor)
        timestamp = int(time.time() if now is None else now)
        with self._db() as db:
            row = db.execute(
                """SELECT * FROM chat_status
                   WHERE status_id=? AND deleted_at IS NULL AND expires_at>?""",
                (str(status_id), timestamp),
            ).fetchone()
            if row is None:
                raise ValueError("status not found")
            viewers = [
                str(item["viewer"])
                for item in db.execute(
                    "SELECT viewer FROM chat_status_viewer WHERE status_id=? ORDER BY viewer",
                    (str(status_id),),
                ).fetchall()
            ]
        result = dict(row)
        if actor != result["owner"] and actor not in viewers:
            raise ValueError("status not found")
        result["viewers"] = viewers
        return result

    def can_view(self, status_id: str, viewer: str, *, now: int | None = None) -> bool:
        viewer = self._principal(viewer)
        timestamp = int(time.time() if now is None else now)
        with self._db() as db:
            row = db.execute(
                """SELECT 1 FROM chat_status s
                   JOIN chat_status_viewer v ON v.status_id=s.status_id
                   WHERE s.status_id=? AND v.viewer=?
                     AND s.deleted_at IS NULL AND s.expires_at>?""",
                (str(status_id), viewer, timestamp),
            ).fetchone()
        return row is not None

    def remove(self, status_id: str, owner: str, *, now: int | None = None) -> None:
        owner = self._principal(owner)
        deleted_at = int(time.time() if now is None else now)
        with self._db() as db:
            cursor = db.execute(
                """UPDATE chat_status SET deleted_at=?
                   WHERE status_id=? AND owner=? AND deleted_at IS NULL""",
                (deleted_at, str(status_id), owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("status not found")
        self.history.record(
            "chat_status_removed",
            owner,
            "chat-status",
            str(status_id),
            {"status_id": str(status_id), "deleted_at": deleted_at},
        )
