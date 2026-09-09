"""SQLite persistence for generic data-quality game sessions and proposals."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VOTE_SOURCES = frozenset({"human", "ai", "system"})


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
                CREATE TABLE IF NOT EXISTS game_challenge (
                    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES game_item(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, answer_type TEXT NOT NULL, prompt TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL, answered_by TEXT, answered_at TEXT,
                    UNIQUE(item_id, kind)
                );
                CREATE TABLE IF NOT EXISTS annotation_proposal (
                    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES game_item(id) ON DELETE CASCADE,
                    field_name TEXT NOT NULL, value_json TEXT NOT NULL, proposed_by TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'human', created_at TEXT NOT NULL,
                    UNIQUE(item_id, field_name, value_json, proposed_by)
                );
                CREATE TABLE IF NOT EXISTS annotation_vote (
                    proposal_id TEXT NOT NULL REFERENCES annotation_proposal(id) ON DELETE CASCADE,
                    voter TEXT NOT NULL, approve INTEGER NOT NULL CHECK(approve IN (0,1)),
                    source TEXT NOT NULL DEFAULT 'human', created_at TEXT NOT NULL,
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
            # Existing installations may have the initial vote table without a
            # source column. Additive migration keeps their human votes valid.
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(annotation_vote)").fetchall()}
            if "source" not in columns:
                db.execute("ALTER TABLE annotation_vote ADD COLUMN source TEXT NOT NULL DEFAULT 'human'")

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
            try:
                db.execute("INSERT INTO game_item(id,session_id,provider,object_ref,resource_class) VALUES(?,?,?,?,?)",
                           (item_id, session_id, provider, object_ref, resource_class))
            except sqlite3.IntegrityError:
                row = db.execute(
                    "SELECT id FROM game_item WHERE session_id=? AND provider=? AND object_ref=?",
                    (session_id, provider, object_ref),
                ).fetchone()
                if row is None:
                    raise
                item_id = str(row["id"])
        return item_id

    def add_challenge(self, item_id: str, kind: str, answer_type: str, prompt: str,
                      payload: dict[str, Any] | None = None) -> str:
        challenge_id = str(uuid.uuid4())
        with self._db() as db:
            try:
                db.execute(
                    "INSERT INTO game_challenge(id,item_id,kind,answer_type,prompt,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (challenge_id, item_id, kind, answer_type, prompt,
                     json.dumps(payload or {}, sort_keys=True, ensure_ascii=False), _now()),
                )
            except sqlite3.IntegrityError:
                row = db.execute(
                    "SELECT id FROM game_challenge WHERE item_id=? AND kind=? AND status='open'",
                    (item_id, kind),
                ).fetchone()
                if row is None:
                    raise
                challenge_id = str(row["id"])
        return challenge_id

    def get_challenge_for_actor(self, challenge_id: str, actor: str) -> dict[str, Any] | None:
        """Return an open challenge only when it belongs to an active local session of actor."""
        with self._db() as db:
            row = self._challenge_row(db, challenge_id, actor)
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
        return result

    @staticmethod
    def _challenge_row(db: sqlite3.Connection, challenge_id: str, actor: str) -> sqlite3.Row | None:
        return db.execute(
            "SELECT c.*, i.session_id, i.provider, i.object_ref, i.resource_class, s.scope, s.created_by "
            "FROM game_challenge c JOIN game_item i ON i.id=c.item_id "
            "JOIN game_session s ON s.id=i.session_id "
            "WHERE c.id=? AND c.status='open' AND s.status='active' AND s.created_by=?",
            (challenge_id, actor),
        ).fetchone()

    def get_proposal_for_actor(self, proposal_id: str, actor: str) -> dict[str, Any] | None:
        """Resolve an opaque proposal only inside the actor's own active local session."""
        with self._db() as db:
            row = db.execute(
                "SELECT p.*, i.session_id, i.provider, i.object_ref, i.resource_class, "
                "s.scope, s.created_by, a.accepted_by, a.mode accepted_mode, a.accepted_at "
                "FROM annotation_proposal p JOIN game_item i ON i.id=p.item_id "
                "JOIN game_session s ON s.id=i.session_id "
                "LEFT JOIN annotation_acceptance a ON a.proposal_id=p.id "
                "WHERE p.id=? AND s.status='active' AND s.scope='local' AND s.created_by=?",
                (proposal_id, actor),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["value"] = json.loads(result.pop("value_json"))
        return result

    @staticmethod
    def _proposal_in_db(db: sqlite3.Connection, item_id: str, field_name: str, value: Any,
                        actor: str, source: str = "human") -> str:
        value_json = json.dumps(value, sort_keys=True, ensure_ascii=False)
        row = db.execute(
            "SELECT id FROM annotation_proposal WHERE item_id=? AND field_name=? AND value_json=? AND proposed_by=?",
            (item_id, field_name, value_json, actor),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        proposal_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO annotation_proposal(id,item_id,field_name,value_json,proposed_by,source,created_at) VALUES(?,?,?,?,?,?,?)",
            (proposal_id, item_id, field_name, value_json, actor, source, _now()),
        )
        return proposal_id

    def answer_challenge(self, challenge_id: str, actor: str, value: Any, *, source: str = "human") -> str:
        """Atomically consume an actor-bound challenge and create its proposal."""
        if source not in VOTE_SOURCES:
            raise ValueError("invalid proposal source")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            challenge = self._challenge_row(db, challenge_id, actor)
            if challenge is None:
                raise ValueError("challenge is not available for this actor")
            changed = db.execute(
                "UPDATE game_challenge SET status='answered', answered_by=?, answered_at=? WHERE id=? AND status='open'",
                (actor, _now(), challenge_id),
            ).rowcount
            if changed != 1:
                raise ValueError("challenge was already answered")
            proposal_id = self._proposal_in_db(
                db, str(challenge["item_id"]), str(challenge["kind"]), value, actor, source,
            )
            self._audit(db, str(challenge["session_id"]), actor, "challenge.answered",
                        {"challenge_id": challenge_id, "proposal_id": proposal_id, "source": source})
        return proposal_id

    def skip_challenge(self, challenge_id: str, actor: str, *, disposition: str = "unknown") -> None:
        """Consume a challenge without creating a proposal for unknown/skip answers."""
        if disposition not in {"unknown", "skip"}:
            raise ValueError("invalid challenge disposition")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            challenge = self._challenge_row(db, challenge_id, actor)
            if challenge is None:
                raise ValueError("challenge is not available for this actor")
            changed = db.execute(
                "UPDATE game_challenge SET status=?, answered_by=?, answered_at=? WHERE id=? AND status='open'",
                (disposition, actor, _now(), challenge_id),
            ).rowcount
            if changed != 1:
                raise ValueError("challenge was already completed")
            self._audit(db, str(challenge["session_id"]), actor, f"challenge.{disposition}",
                        {"challenge_id": challenge_id})

    def propose(self, item_id: str, field_name: str, value: Any, actor: str, source: str = "human") -> str:
        if source not in VOTE_SOURCES:
            raise ValueError("invalid proposal source")
        with self._db() as db:
            proposal_id = self._proposal_in_db(db, item_id, field_name, value, actor, source)
            row = db.execute("SELECT session_id FROM game_item WHERE id=?", (item_id,)).fetchone()
            self._audit(db, str(row["session_id"]) if row else None, actor, "proposal.created",
                        {"proposal_id": proposal_id, "field_name": field_name, "source": source})
        return proposal_id

    def vote(self, proposal_id: str, voter: str, approve: bool, *, source: str = "human") -> None:
        """Record one vote per identity while preserving its human/AI provenance."""
        if source not in VOTE_SOURCES:
            raise ValueError("invalid vote source")
        with self._db() as db:
            db.execute(
                "INSERT INTO annotation_vote(proposal_id,voter,approve,source,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(proposal_id,voter) DO UPDATE SET approve=excluded.approve, source=excluded.source, created_at=excluded.created_at",
                (proposal_id, voter, int(approve), source, _now()),
            )
            row = db.execute(
                "SELECT i.session_id FROM annotation_proposal p JOIN game_item i ON i.id=p.item_id WHERE p.id=?",
                (proposal_id,),
            ).fetchone()
            self._audit(db, str(row["session_id"]) if row else None, voter, "proposal.voted",
                        {"proposal_id": proposal_id, "approve": bool(approve), "source": source})

    def consensus(self, proposal_id: str, min_votes: int = 3, ratio: float = 0.75,
                  *, include_nonhuman: bool = False) -> dict[str, Any]:
        """Calculate consensus from human votes unless explicitly requested otherwise."""
        with self._db() as db:
            if include_nonhuman:
                row = db.execute(
                    "SELECT COUNT(*) total, COALESCE(SUM(approve),0) approvals FROM annotation_vote WHERE proposal_id=?",
                    (proposal_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT COUNT(*) total, COALESCE(SUM(approve),0) approvals FROM annotation_vote WHERE proposal_id=? AND source='human'",
                    (proposal_id,),
                ).fetchone()
        total, approvals = int(row["total"]), int(row["approvals"])
        score = approvals / total if total else 0.0
        return {"total": total, "approvals": approvals, "ratio": score,
                "reached": total >= min_votes and score >= ratio}

    def accept(self, proposal_id: str, actor: str, mode: str = "manual") -> None:
        """Record acceptance only. Provider code must re-check write ACL before applying it."""
        with self._db() as db:
            db.execute("INSERT INTO annotation_acceptance(proposal_id,accepted_by,mode,accepted_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(proposal_id) DO NOTHING", (proposal_id, actor, mode, _now()))
            row = db.execute(
                "SELECT i.session_id FROM annotation_proposal p JOIN game_item i ON i.id=p.item_id WHERE p.id=?",
                (proposal_id,),
            ).fetchone()
            self._audit(db, str(row["session_id"]) if row else None, actor, "proposal.accepted",
                        {"proposal_id": proposal_id, "mode": mode})

    def audit(self, session_id: str | None, actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
        with self._db() as db:
            self._audit(db, session_id, actor, action, detail or {})

    @staticmethod
    def _audit(db: sqlite3.Connection, session_id: str | None, actor: str, action: str, detail: dict[str, Any]) -> None:
        db.execute("INSERT INTO game_audit(session_id,actor,action,detail_json,created_at) VALUES(?,?,?,?,?)",
                   (session_id, actor, action, json.dumps(detail, sort_keys=True, ensure_ascii=False), _now()))
