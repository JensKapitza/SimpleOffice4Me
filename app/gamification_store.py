"""SQLite persistence for generic data-quality game sessions and proposals."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GamificationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.control = self.root / ".simpleoffice"
        self.control.mkdir(parents=True, exist_ok=True)
        self.path = self.control / "gamification.sqlite3"
        self.initialize()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS game_session (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, title TEXT NOT NULL,
                    policy_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_item (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES game_session(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL, object_ref TEXT NOT NULL, resource_class TEXT NOT NULL DEFAULT '',
                    UNIQUE(session_id, provider, object_ref)
                );
                CREATE TABLE IF NOT EXISTS annotation_proposal (
                    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES game_item(id) ON DELETE CASCADE,
                    field_name TEXT NOT NULL, value_json TEXT NOT NULL, proposed_by TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'human', created_at TEXT NOT NULL,
                    UNIQUE(item_id, field_name, value_json, proposed_by)
                );
                CREATE TABLE IF NOT EXISTS annotation_vote (
                    proposal_id TEXT NOT NULL REFERENCES annotation_proposal(id) ON DELETE CASCADE,
                    voter TEXT NOT NULL, approve INTEGER NOT NULL CHECK(approve IN (0,1)), created_at TEXT NOT NULL,
                    PRIMARY KEY(proposal_id, voter)
                );
                CREATE TABLE IF NOT EXISTS annotation_acceptance (
                    proposal_id TEXT PRIMARY KEY REFERENCES annotation_proposal(id) ON DELETE CASCADE,
                    accepted_by TEXT NOT NULL, mode TEXT NOT NULL, accepted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, actor TEXT NOT NULL,
                    action TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
            """)

    def create_session(self, title: str, scope: str, created_by: str, policy: dict[str, Any]) -> str:
        session_id = str(uuid.uuid4())
        with self._db() as db:
            db.execute("INSERT INTO game_session(id,scope,title,policy_json,created_by,created_at) VALUES(?,?,?,?,?,?)",
                       (session_id, scope, title.strip(), json.dumps(policy, sort_keys=True), created_by, _now()))
            self._audit(db, session_id, created_by, "session.created", {"scope": scope})
        return session_id

    def add_item(self, session_id: str, provider: str, object_ref: str, resource_class: str = "") -> str:
        item_id = str(uuid.uuid4())
        with self._db() as db:
            db.execute("INSERT INTO game_item(id,session_id,provider,object_ref,resource_class) VALUES(?,?,?,?,?)",
                       (item_id, session_id, provider, object_ref, resource_class))
        return item_id

    def propose(self, item_id: str, field_name: str, value: Any, actor: str, source: str = "human") -> str:
        proposal_id = str(uuid.uuid4())
        value_json = json.dumps(value, sort_keys=True, ensure_ascii=False)
        with self._db() as db:
            db.execute("INSERT INTO annotation_proposal(id,item_id,field_name,value_json,proposed_by,source,created_at) VALUES(?,?,?,?,?,?,?)",
                       (proposal_id, item_id, field_name, value_json, actor, source, _now()))
        return proposal_id

    def vote(self, proposal_id: str, voter: str, approve: bool) -> None:
        with self._db() as db:
            db.execute("INSERT INTO annotation_vote(proposal_id,voter,approve,created_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(proposal_id,voter) DO UPDATE SET approve=excluded.approve, created_at=excluded.created_at",
                       (proposal_id, voter, int(approve), _now()))

    def consensus(self, proposal_id: str, min_votes: int = 3, ratio: float = 0.75) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT COUNT(*) total, COALESCE(SUM(approve),0) approvals FROM annotation_vote WHERE proposal_id=?",
                             (proposal_id,)).fetchone()
        total, approvals = int(row["total"]), int(row["approvals"])
        score = approvals / total if total else 0.0
        return {"total": total, "approvals": approvals, "ratio": score,
                "reached": total >= min_votes and score >= ratio}

    def accept(self, proposal_id: str, actor: str, mode: str = "manual") -> None:
        """Record acceptance only. Provider code must re-check write ACL before applying it."""
        with self._db() as db:
            db.execute("INSERT INTO annotation_acceptance(proposal_id,accepted_by,mode,accepted_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(proposal_id) DO NOTHING", (proposal_id, actor, mode, _now()))

    def audit(self, session_id: str | None, actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
        with self._db() as db:
            self._audit(db, session_id, actor, action, detail or {})

    @staticmethod
    def _audit(db: sqlite3.Connection, session_id: str | None, actor: str, action: str, detail: dict[str, Any]) -> None:
        db.execute("INSERT INTO game_audit(session_id,actor,action,detail_json,created_at) VALUES(?,?,?,?,?)",
                   (session_id, actor, action, json.dumps(detail, sort_keys=True, ensure_ascii=False), _now()))
