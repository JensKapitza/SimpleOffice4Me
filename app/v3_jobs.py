"""Persistent optional background jobs for SimpleOffice 3.0."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Callable, Mapping

from .sqlite_utils import connect as sqlite_connect


TERMINAL_STATES={"succeeded","failed","cancelled"}
CLAIMABLE_STATES={"queued","retry_scheduled","running"}


class RetryableJobError(RuntimeError):
    pass


class PermanentJobError(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    job_id: str
    kind: str
    state: str
    idempotency_key: str
    principal: str
    payload: dict
    priority: int
    attempt: int
    max_attempts: int
    available_at: int
    lease_owner: str
    lease_until: int
    created_at: int
    started_at: int
    finished_at: int
    updated_at: int
    last_error: str


def _json(value: Mapping | None) -> str:
    encoded=json.dumps(dict(value or {}),ensure_ascii=False,sort_keys=True,separators=(",",":"))
    if len(encoded.encode("utf-8")) > 64*1024:
        raise ValueError("job payload is too large")
    return encoded


class JobStore:
    """Small SQLite queue with leases, crash recovery and idempotent enqueue."""

    def __init__(self, root: str | Path):
        self.root=Path(root).expanduser().resolve()
        self.path=self.root/".simpleoffice-meta"/"v3-jobs.sqlite3"
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
            CREATE TABLE IF NOT EXISTS v3_job(
                job_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                principal TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                available_at INTEGER NOT NULL,
                lease_owner TEXT NOT NULL DEFAULT '',
                lease_until INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                started_at INTEGER NOT NULL DEFAULT 0,
                finished_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                last_error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_v3_job_claim
              ON v3_job(state,available_at,priority DESC,created_at);
            CREATE INDEX IF NOT EXISTS ix_v3_job_updated
              ON v3_job(updated_at DESC,job_id);
            """)

    @staticmethod
    def _record(row: sqlite3.Row) -> Job:
        return Job(
            str(row["job_id"]),str(row["kind"]),str(row["state"]),str(row["idempotency_key"]),
            str(row["principal"]),json.loads(row["payload_json"] or "{}"),int(row["priority"]),
            int(row["attempt"]),int(row["max_attempts"]),int(row["available_at"]),
            str(row["lease_owner"]),int(row["lease_until"]),int(row["created_at"]),
            int(row["started_at"]),int(row["finished_at"]),int(row["updated_at"]),str(row["last_error"]),
        )

    def enqueue(self, kind: str, payload: Mapping | None, principal: str, *, idempotency_key: str, priority: int=0, max_attempts: int=3) -> Job:
        kind=str(kind).strip(); principal=str(principal).strip(); key=str(idempotency_key).strip()
        if not kind or not principal or not key:
            raise ValueError("kind, principal and idempotency key are required")
        now=int(time.time()); encoded=_json(payload)
        attempts=max(1,min(20,int(max_attempts))); prio=max(-100,min(100,int(priority)))
        with self._db() as db:
            existing=db.execute("SELECT * FROM v3_job WHERE idempotency_key=?",(key,)).fetchone()
            if existing:
                return self._record(existing)
            job_id=uuid.uuid4().hex
            db.execute(
                """INSERT INTO v3_job(job_id,kind,state,idempotency_key,principal,payload_json,
                   priority,max_attempts,available_at,created_at,updated_at)
                   VALUES(?,?,'queued',?,?,?,?,?,?,?,?)""",
                (job_id,kind,key,principal,encoded,prio,attempts,0,now,now),
            )
            row=db.execute("SELECT * FROM v3_job WHERE job_id=?",(job_id,)).fetchone()
        return self._record(row)

    def get(self, job_id: str) -> Job | None:
        with self._db() as db:
            row=db.execute("SELECT * FROM v3_job WHERE job_id=?",(str(job_id),)).fetchone()
        return self._record(row) if row else None

    def claim(self, worker: str, *, lease_seconds: int=60, now: int | None=None) -> Job | None:
        owner=str(worker).strip()
        if not owner:
            raise ValueError("worker id is required")
        current=int(time.time()) if now is None else int(now)
        lease_until=current+max(5,min(3600,int(lease_seconds)))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute(
                """SELECT * FROM v3_job
                   WHERE state IN ('queued','retry_scheduled','running')
                     AND available_at<=?
                     AND (state!='running' OR lease_until<=? OR lease_owner=?)
                   ORDER BY priority DESC,created_at,job_id LIMIT 1""",
                (current,current,owner),
            ).fetchone()
            if not row:
                return None
            attempt=int(row["attempt"])+1
            started=int(row["started_at"]) or current
            db.execute(
                """UPDATE v3_job SET state='running',attempt=?,lease_owner=?,lease_until=?,
                   started_at=?,updated_at=?,last_error='' WHERE job_id=?""",
                (attempt,owner,lease_until,started,current,row["job_id"]),
            )
            updated=db.execute("SELECT * FROM v3_job WHERE job_id=?",(row["job_id"],)).fetchone()
        return self._record(updated)

    def succeed(self, job_id: str, *, now: int | None=None) -> Job:
        return self._terminal(job_id,"succeeded","",now)

    def fail(self, job_id: str, error: str, *, retryable: bool, now: int | None=None) -> Job:
        current=int(time.time()) if now is None else int(now)
        job=self.get(job_id)
        if job is None:
            raise LookupError("unknown job")
        message=" ".join(str(error).replace("\x00"," ").split())[:1000]
        if retryable and job.attempt < job.max_attempts:
            delay=min(3600, max(5, 5*(2 ** max(0,job.attempt-1))))
            with self._db() as db:
                db.execute(
                    """UPDATE v3_job SET state='retry_scheduled',available_at=?,lease_owner='',
                       lease_until=0,last_error=?,updated_at=? WHERE job_id=?""",
                    (current+delay,message,current,job_id),
                )
            return self.get(job_id)
        return self._terminal(job_id,"failed",message,current)

    def _terminal(self, job_id: str, state: str, error: str="", now: int | None=None) -> Job:
        if state not in TERMINAL_STATES:
            raise ValueError("invalid terminal state")
        current=int(time.time()) if now is None else int(now)
        with self._db() as db:
            row=db.execute("SELECT job_id FROM v3_job WHERE job_id=?",(job_id,)).fetchone()
            if not row:
                raise LookupError("unknown job")
            db.execute(
                """UPDATE v3_job SET state=?,lease_owner='',lease_until=0,finished_at=?,
                   updated_at=?,last_error=? WHERE job_id=?""",
                (state,current,current,str(error)[:1000],job_id),
            )
        return self.get(job_id)

    def cancel(self, job_id: str, *, now: int | None=None) -> Job:
        job=self.get(job_id)
        if job is None:
            raise LookupError("unknown job")
        if job.state=="running":
            raise ValueError("running jobs require cooperative cancellation")
        if job.state in TERMINAL_STATES:
            return job
        return self._terminal(job_id,"cancelled","",now)

    def retry_failed(self, job_id: str, *, now: int | None=None) -> Job:
        current=int(time.time()) if now is None else int(now)
        job=self.get(job_id)
        if job is None:
            raise LookupError("unknown job")
        if job.state!="failed":
            raise ValueError("only failed jobs can be retried")
        with self._db() as db:
            db.execute(
                """UPDATE v3_job SET state='queued',attempt=0,available_at=?,finished_at=0,
                   lease_owner='',lease_until=0,last_error='',updated_at=? WHERE job_id=?""",
                (current,current,job_id),
            )
        return self.get(job_id)

    def list(self, *, state: str="", kind: str="", limit: int=100) -> list[Job]:
        size=max(1,min(500,int(limit)))
        with self._db() as db:
            rows=db.execute(
                """SELECT * FROM v3_job
                   WHERE (?='' OR state=?) AND (?='' OR kind=?)
                   ORDER BY updated_at DESC,job_id DESC LIMIT ?""",
                (state,state,kind,kind,size),
            ).fetchall()
        return [self._record(row) for row in rows]

    def metrics(self, *, now: int | None=None) -> dict[str, int]:
        current=int(time.time()) if now is None else int(now)
        with self._db() as db:
            counts={row["state"]:int(row["n"]) for row in db.execute("SELECT state,COUNT(*) n FROM v3_job GROUP BY state")}
            oldest=db.execute("SELECT MIN(created_at) FROM v3_job WHERE state IN ('queued','retry_scheduled')").fetchone()[0]
        return {"queued":counts.get("queued",0)+counts.get("retry_scheduled",0),"running":counts.get("running",0),"failed":counts.get("failed",0),"oldest_age_seconds":max(0,current-int(oldest)) if oldest else 0}


Handler=Callable[[Job],None]


class JobWorker:
    def __init__(self, store: JobStore, handlers: Mapping[str,Handler], worker_id: str):
        self.store=store; self.handlers=dict(handlers); self.worker_id=str(worker_id).strip()
        if not self.worker_id:
            raise ValueError("worker id is required")

    def run_once(self) -> Job | None:
        job=self.store.claim(self.worker_id)
        if job is None:
            return None
        handler=self.handlers.get(job.kind)
        if handler is None:
            return self.store.fail(job.job_id,f"no handler registered for {job.kind}",retryable=False)
        try:
            handler(job)
        except RetryableJobError as exc:
            return self.store.fail(job.job_id,str(exc),retryable=True)
        except PermanentJobError as exc:
            return self.store.fail(job.job_id,str(exc),retryable=False)
        except Exception:
            return self.store.fail(job.job_id,"job handler failed",retryable=True)
        return self.store.succeed(job.job_id)


def document_reindex_handler(root: str | Path) -> Handler:
    """Pilot adapter: run the existing document index reconciliation through the queue."""
    root=Path(root).expanduser().resolve()
    def run(job: Job) -> None:
        from .document_store import DocumentStore
        try:
            DocumentStore(root).scan()
        except (OSError,sqlite3.Error) as exc:
            raise RetryableJobError("document index temporarily unavailable") from exc
        except ValueError as exc:
            raise PermanentJobError("document index rejected the request") from exc
    return run


def default_handlers(root: str | Path) -> dict[str,Handler]:
    return {"documents.reindex":document_reindex_handler(root)}
