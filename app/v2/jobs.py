"""Persistent V2 jobs and federation desired-state transfer intents."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .contracts import ErrorCode, JobRecord, JobState, OperationResult
from .authorization import AuthorizationStore, GrantRight
from .federation_policy import FederationPolicyStore


SCHEMA_VERSION = 1


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class FederationTransferIntent:
    source_peer: str
    target_peer: str
    object_refs: tuple[str, ...]
    authorization_ref: str
    expires_at: int
    desired_state: str = "verified_present"

    def __post_init__(self) -> None:
        if not self.source_peer or not self.target_peer or self.source_peer == self.target_peer:
            raise ValueError("distinct source and target peers are required")
        if not self.object_refs or any(not str(item).strip() for item in self.object_refs):
            raise ValueError("at least one object reference is required")
        if len(set(self.object_refs)) != len(self.object_refs):
            raise ValueError("duplicate object references are not allowed")
        if not self.authorization_ref:
            raise ValueError("authorization reference is required")
        if self.expires_at <= int(time.time()):
            raise ValueError("transfer authorization must expire in the future")
        if self.desired_state != "verified_present":
            raise ValueError("unsupported federation desired state")


class PersistentJobStore:
    """SQLite job store with idempotency and bounded worker leases."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / ".simpleoffice-v2"
        self.path = self.control / "jobs.sqlite3"
        self.control.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS job_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job(
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_until INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS job_state_updated
                    ON job(state, updated_at);
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO job_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    def put(self, job: JobRecord) -> OperationResult[JobRecord]:
        now = int(time.time())
        try:
            with self._db() as db:
                existing = db.execute(
                    "SELECT * FROM job WHERE idempotency_key=?",
                    (job.idempotency_key,),
                ).fetchone()
                if existing:
                    return OperationResult.success(self._record(existing))
                db.execute(
                    """INSERT INTO job(
                        job_id,kind,state,idempotency_key,payload_json,attempt,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        job.job_id,
                        job.kind,
                        job.state.value,
                        job.idempotency_key,
                        _json(dict(job.payload)),
                        int(job.attempt),
                        now,
                        now,
                    ),
                )
            return self.get(job.job_id)
        except sqlite3.IntegrityError as exc:
            return OperationResult.failure(ErrorCode.CONFLICT, str(exc))

    def get(self, job_id: str) -> OperationResult[JobRecord]:
        with self._db() as db:
            row = db.execute("SELECT * FROM job WHERE job_id=?", (str(job_id),)).fetchone()
        if not row:
            return OperationResult.failure(ErrorCode.NOT_FOUND, "job not found")
        return OperationResult.success(self._record(row))

    def claim(self, worker: str, *, lease_seconds: int = 60) -> OperationResult[JobRecord]:
        owner = str(worker or "").strip()
        if not owner:
            return OperationResult.failure(ErrorCode.INVALID_INPUT, "worker id is required")
        now = int(time.time())
        lease_until = now + max(1, min(int(lease_seconds), 3600))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM job
                   WHERE state IN ('queued','waiting','running')
                     AND (lease_until <= ? OR lease_owner = ?)
                   ORDER BY updated_at, created_at
                   LIMIT 1""",
                (now, owner),
            ).fetchone()
            if not row:
                return OperationResult.failure(ErrorCode.NOT_FOUND, "no claimable job")
            attempt = int(row["attempt"]) + 1
            db.execute(
                """UPDATE job SET state='running',attempt=?,lease_owner=?,
                   lease_until=?,updated_at=? WHERE job_id=?""",
                (attempt, owner, lease_until, now, row["job_id"]),
            )
        return self.get(str(row["job_id"]))

    def transition(
        self,
        job_id: str,
        state: JobState,
        *,
        payload: dict[str, Any] | None = None,
        error: str = "",
        clear_lease: bool = True,
    ) -> OperationResult[JobRecord]:
        current = self.get(job_id)
        if not current.ok:
            return current
        now = int(time.time())
        with self._db() as db:
            row = db.execute("SELECT payload_json,lease_owner,lease_until FROM job WHERE job_id=?", (job_id,)).fetchone()
            current_payload = json.loads(row["payload_json"])
            next_payload = dict(current_payload)
            if payload:
                next_payload.update(payload)
            db.execute(
                """UPDATE job SET state=?,payload_json=?,last_error=?,
                   lease_owner=?,lease_until=?,updated_at=? WHERE job_id=?""",
                (
                    state.value,
                    _json(next_payload),
                    str(error)[:2000],
                    "" if clear_lease else str(row["lease_owner"]),
                    0 if clear_lease else int(row["lease_until"]),
                    now,
                    job_id,
                ),
            )
        return self.get(job_id)

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=str(row["job_id"]),
            kind=str(row["kind"]),
            state=JobState(str(row["state"])),
            idempotency_key=str(row["idempotency_key"]),
            payload=json.loads(row["payload_json"]),
            attempt=int(row["attempt"]),
        )


class FederationJobService:
    KIND = "federation.transfer.v2"

    def __init__(self, store: PersistentJobStore):
        self.store = store

    def create_transfer(
        self,
        intent: FederationTransferIntent,
        *,
        idempotency_key: str,
        authorization_store: AuthorizationStore | None = None,
        policy_store: FederationPolicyStore | None = None,
        route: tuple[str, ...] | list[str] | None = None,
        policy_scope: str = "relay",
    ) -> OperationResult[JobRecord]:
        if policy_store is not None:
            checked_route = list(route) if route is not None else [intent.source_peer, intent.target_peer]
            decision = policy_store.decision(checked_route, scope=policy_scope)
            if not decision.allowed:
                return OperationResult.failure(
                    ErrorCode.FORBIDDEN,
                    f"federation policy denied transfer: {decision.reason}:{decision.blocked_peer}",
                )
        if authorization_store is not None:
            grant = authorization_store.get(intent.authorization_ref)
            if grant is None or grant.expires_at < intent.expires_at:
                return OperationResult.failure(
                    ErrorCode.FORBIDDEN,
                    "transfer lifetime exceeds its authorization",
                )
            for object_ref in intent.object_refs:
                if not authorization_store.allows(
                    intent.authorization_ref,
                    subject=intent.source_peer,
                    right=GrantRight.RELAY,
                    object_ref=object_ref,
                ):
                    return OperationResult.failure(
                        ErrorCode.FORBIDDEN,
                        "transfer authorization does not allow relay for the complete object scope",
                    )
        payload = {
            "source_peer": intent.source_peer,
            "target_peer": intent.target_peer,
            "object_refs": list(intent.object_refs),
            "authorization_ref": intent.authorization_ref,
            "expires_at": intent.expires_at,
            "desired_state": intent.desired_state,
            "verified_object_refs": [],
        }
        job = JobRecord(
            job_id=str(uuid.uuid4()),
            kind=self.KIND,
            state=JobState.QUEUED,
            idempotency_key=str(idempotency_key),
            payload=payload,
        )
        return self.store.put(job)

    def mark_verified(
        self,
        job_id: str,
        object_ref: str,
        *,
        observed_via: str,
        authorization_store: AuthorizationStore | None = None,
    ) -> OperationResult[JobRecord]:
        current = self.store.get(job_id)
        if not current.ok:
            return current
        job = current.value
        if job.kind != self.KIND:
            return OperationResult.failure(ErrorCode.INVALID_INPUT, "job is not a federation transfer")
        payload = dict(job.payload)
        if int(payload.get("expires_at", 0)) < int(time.time()):
            return self.store.transition(job_id, JobState.FAILED, error="transfer authorization expired")
        if authorization_store is not None:
            authorization_ref = str(payload.get("authorization_ref") or "")
            source_peer = str(payload.get("source_peer") or "")
            if not authorization_store.allows(
                authorization_ref,
                subject=source_peer,
                right=GrantRight.RELAY,
                object_ref=object_ref,
            ):
                return self.store.transition(
                    job_id,
                    JobState.FAILED,
                    error="transfer authorization is no longer effective",
                )
        expected = {str(item) for item in payload.get("object_refs", [])}
        if object_ref not in expected:
            return OperationResult.failure(ErrorCode.FORBIDDEN, "object is outside transfer scope")
        verified = {str(item) for item in payload.get("verified_object_refs", [])}
        verified.add(object_ref)
        state = JobState.SUCCEEDED if verified >= expected else JobState.WAITING
        return self.store.transition(
            job_id,
            state,
            payload={
                "verified_object_refs": sorted(verified),
                "last_observed_via": str(observed_via or "unknown")[:120],
            },
        )
