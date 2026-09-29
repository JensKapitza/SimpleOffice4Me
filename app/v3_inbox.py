"""Universal V3 inbox provenance and workflow state."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Mapping

from .sqlite_utils import connect as sqlite_connect
from .v3_capabilities import enabled as capability_enabled


STATES={
    "received","validating","quarantined","imported","review",
    "accepted","rejected","failed",
}
ASSIGNMENT_TYPES={"contact","company","project","task"}


@dataclass(frozen=True)
class InboxItem:
    item_id: str
    source: str
    source_key: str
    original_name: str
    mime_type: str
    sha256: str
    size: int
    actor: str
    status: str
    document_id: str
    malware_status: str
    error: str
    received_at: int
    updated_at: int
    assignments: tuple[dict, ...]


class InboxStore:
    def __init__(self, root: str | Path):
        self.root=Path(root).expanduser().resolve()
        self.path=self.root/".simpleoffice-meta"/"v3-inbox.sqlite3"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self._initialize()

    def _db(self):
        db=sqlite_connect(self.path,timeout=30)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self):
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS v3_inbox_item(
                item_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_key TEXT NOT NULL,
                original_name TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                actor TEXT NOT NULL,
                status TEXT NOT NULL,
                document_id TEXT NOT NULL DEFAULT '',
                malware_status TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                received_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(source,source_key)
            );
            CREATE INDEX IF NOT EXISTS ix_v3_inbox_status
              ON v3_inbox_item(status,updated_at DESC,item_id);
            CREATE INDEX IF NOT EXISTS ix_v3_inbox_source
              ON v3_inbox_item(source,received_at DESC,item_id);
            CREATE TABLE IF NOT EXISTS v3_inbox_step(
                step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                step TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                occurred_at INTEGER NOT NULL,
                FOREIGN KEY(item_id) REFERENCES v3_inbox_item(item_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_v3_inbox_step_item
              ON v3_inbox_step(item_id,step_id);
            CREATE TABLE IF NOT EXISTS v3_inbox_assignment(
                item_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(item_id,target_type,target_id),
                FOREIGN KEY(item_id) REFERENCES v3_inbox_item(item_id) ON DELETE CASCADE
            );
            """)

    @staticmethod
    def _item(row: sqlite3.Row, assignments: list[sqlite3.Row] | None=None) -> InboxItem:
        return InboxItem(
            str(row["item_id"]),str(row["source"]),str(row["source_key"]),
            str(row["original_name"]),str(row["mime_type"]),str(row["sha256"]),
            int(row["size"]),str(row["actor"]),str(row["status"]),
            str(row["document_id"]),str(row["malware_status"]),str(row["error"]),
            int(row["received_at"]),int(row["updated_at"]),
            tuple({
                "target_type":str(item["target_type"]),
                "target_id":str(item["target_id"]),
                "actor":str(item["actor"]),
                "created_at":int(item["created_at"]),
            } for item in (assignments or [])),
        )

    def begin(self, source: str, source_key: str, original_name: str, actor: str, *, mime_type: str="") -> InboxItem:
        source=str(source).strip()[:80]; source_key=str(source_key).strip()[:500]
        original_name=Path(str(original_name)).name[:255]; actor=str(actor).strip()[:200]
        if not source or not source_key or not original_name or not actor:
            raise ValueError("source, source key, name and actor are required")
        now=int(time.time())
        with self._db() as db:
            existing=db.execute(
                "SELECT * FROM v3_inbox_item WHERE source=? AND source_key=?",
                (source,source_key),
            ).fetchone()
            if existing:
                assignments=db.execute(
                    "SELECT * FROM v3_inbox_assignment WHERE item_id=? ORDER BY created_at,target_type,target_id",
                    (existing["item_id"],),
                ).fetchall()
                return self._item(existing,assignments)
            item_id=uuid.uuid4().hex
            db.execute(
                """INSERT INTO v3_inbox_item(
                    item_id,source,source_key,original_name,mime_type,actor,status,
                    received_at,updated_at
                ) VALUES(?,?,?,?,?,?,'received',?,?)""",
                (item_id,source,source_key,original_name,str(mime_type)[:160],actor,now,now),
            )
            db.execute(
                "INSERT INTO v3_inbox_step(item_id,step,status,occurred_at) VALUES(?,'received','ok',?)",
                (item_id,now),
            )
        return self.get(item_id)

    def transition(self, item_id: str, status: str, *, step: str, detail: str="", error: str="") -> InboxItem:
        status=str(status).strip()
        if status not in STATES:
            raise ValueError("invalid inbox status")
        now=int(time.time())
        with self._db() as db:
            if not db.execute("SELECT 1 FROM v3_inbox_item WHERE item_id=?",(item_id,)).fetchone():
                raise LookupError("unknown inbox item")
            db.execute(
                "UPDATE v3_inbox_item SET status=?,error=?,updated_at=? WHERE item_id=?",
                (status,str(error)[:1000],now,item_id),
            )
            db.execute(
                "INSERT INTO v3_inbox_step(item_id,step,status,detail,occurred_at) VALUES(?,?,?,?,?)",
                (item_id,str(step)[:100],status,str(detail)[:1000],now),
            )
        return self.get(item_id)

    def complete_import(self, item_id: str, *, document_id: str, sha256: str, size: int, malware_status: str) -> InboxItem:
        sha=str(sha256).strip().casefold()
        if sha and (len(sha)!=64 or any(ch not in "0123456789abcdef" for ch in sha)):
            raise ValueError("invalid sha256")
        now=int(time.time())
        with self._db() as db:
            if not db.execute("SELECT 1 FROM v3_inbox_item WHERE item_id=?",(item_id,)).fetchone():
                raise LookupError("unknown inbox item")
            db.execute(
                """UPDATE v3_inbox_item SET status='accepted',document_id=?,sha256=?,size=?,
                   malware_status=?,error='',updated_at=? WHERE item_id=?""",
                (str(document_id)[:200],sha,max(0,int(size)),str(malware_status)[:80],now,item_id),
            )
            for step,status,detail in (
                ("validate","ok","input validated"),
                ("malware_check","ok" if malware_status=="clean" else "skipped",malware_status),
                ("persist_import","ok",str(document_id)[:200]),
                ("metadata_hash","ok",sha),
                ("accept","ok","document accepted"),
            ):
                db.execute(
                    "INSERT INTO v3_inbox_step(item_id,step,status,detail,occurred_at) VALUES(?,?,?,?,?)",
                    (item_id,step,status,detail,now),
                )
        return self.get(item_id)

    def get(self, item_id: str) -> InboxItem:
        with self._db() as db:
            row=db.execute("SELECT * FROM v3_inbox_item WHERE item_id=?",(item_id,)).fetchone()
            if not row:
                raise LookupError("unknown inbox item")
            assignments=db.execute(
                "SELECT * FROM v3_inbox_assignment WHERE item_id=? ORDER BY created_at,target_type,target_id",
                (item_id,),
            ).fetchall()
        return self._item(row,assignments)

    def steps(self, item_id: str) -> list[dict]:
        with self._db() as db:
            rows=db.execute(
                "SELECT step,status,detail,occurred_at FROM v3_inbox_step WHERE item_id=? ORDER BY step_id",
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list(self, *, source: str="", status: str="", limit: int=200) -> list[InboxItem]:
        size=max(1,min(500,int(limit)))
        with self._db() as db:
            rows=db.execute(
                """SELECT * FROM v3_inbox_item
                   WHERE (?='' OR source=?) AND (?='' OR status=?)
                   ORDER BY received_at DESC,item_id DESC LIMIT ?""",
                (source,source,status,status,size),
            ).fetchall()
            result=[]
            for row in rows:
                assignments=db.execute(
                    "SELECT * FROM v3_inbox_assignment WHERE item_id=? ORDER BY created_at,target_type,target_id",
                    (row["item_id"],),
                ).fetchall()
                result.append(self._item(row,assignments))
        return result

    def assign(self, item_id: str, target_type: str, target_id: str, actor: str) -> InboxItem:
        target_type=str(target_type).strip()
        target_id=str(target_id).strip()[:200]
        actor=str(actor).strip()[:200]
        if target_type not in ASSIGNMENT_TYPES or not target_id or not actor:
            raise ValueError("invalid inbox assignment")
        item=self.get(item_id)
        self._validate_target(target_type,target_id,actor)
        now=int(time.time())
        with self._db() as db:
            db.execute(
                """INSERT OR IGNORE INTO v3_inbox_assignment(item_id,target_type,target_id,actor,created_at)
                   VALUES(?,?,?,?,?)""",
                (item_id,target_type,target_id,actor,now),
            )
        if item.document_id:
            try:
                from .document_store import DocumentStore
                DocumentStore(self.root).set_attribute(
                    item.document_id,
                    "v3_inbox_assignments",
                    [
                        {
                            "target_type":row["target_type"],
                            "target_id":row["target_id"],
                        }
                        for row in self._assignment_rows(item_id)
                    ],
                    actor,
                )
            except (OSError,ValueError):
                pass
        return self.get(item_id)

    def _assignment_rows(self,item_id: str) -> list[dict]:
        with self._db() as db:
            rows=db.execute(
                "SELECT target_type,target_id FROM v3_inbox_assignment WHERE item_id=? ORDER BY target_type,target_id",
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _validate_target(self,target_type: str,target_id: str,actor: str) -> None:
        if target_type in {"contact","company"}:
            from .contact_store import ContactStore
            ContactStore(self.root).get(target_id,actor)
            return
        if target_type=="project":
            from .project_store import ProjectStore
            ProjectStore(self.root).project(target_id)
            return
        if target_type=="task":
            from .todo_store import TodoStore
            if not any(str(row.get("todo_id") or row.get("id") or "")==target_id for row in TodoStore(self.root).items(actor)):
                raise ValueError("unknown task")
            return
        raise ValueError("unsupported assignment target")


def record_completed_best_effort(
    root: str | Path,
    *,
    source: str,
    source_key: str,
    original_name: str,
    actor: str,
    document_id: str,
    sha256: str,
    size: int,
    malware_status: str,
    mime_type: str="",
) -> InboxItem | None:
    if not capability_enabled("v3.inbox"):
        return None
    try:
        store=InboxStore(root)
        item=store.begin(source,source_key,original_name,actor,mime_type=mime_type)
        return store.complete_import(
            item.item_id,
            document_id=document_id,
            sha256=sha256,
            size=size,
            malware_status=malware_status,
        )
    except Exception:
        return None
