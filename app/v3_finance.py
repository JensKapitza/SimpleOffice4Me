"""Additive business-document lifecycle for SimpleOffice 3.0."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Any, Mapping

from .sqlite_utils import connect as sqlite_connect


KINDS = {"offer", "order", "delivery_note", "invoice"}
GENERIC_KINDS = {"offer", "order", "delivery_note"}
SUCCESSORS = {
    "offer": {"order"},
    "order": {"delivery_note", "invoice"},
    "delivery_note": {"invoice"},
    "invoice": set(),
}
TRANSITIONS = {
    "offer": {
        "draft": {"final", "cancelled"},
        "final": {"sent", "cancelled"},
        "sent": {"accepted", "rejected", "cancelled"},
        "accepted": set(),
        "rejected": set(),
        "cancelled": set(),
    },
    "order": {
        "draft": {"confirmed", "cancelled"},
        "confirmed": {"fulfilled", "cancelled"},
        "fulfilled": set(),
        "cancelled": set(),
    },
    "delivery_note": {
        "draft": {"final", "cancelled"},
        "final": {"delivered", "cancelled"},
        "delivered": set(),
        "cancelled": set(),
    },
    "invoice": {
        "draft": {"finalizing", "cancelled"},
        "finalizing": {"final", "failed"},
        "failed": {"draft", "cancelled"},
        "final": {"sent", "partial", "paid", "overdue", "written_off"},
        "sent": {"partial", "paid", "overdue", "written_off"},
        "partial": {"paid", "overdue", "written_off"},
        "overdue": {"partial", "paid", "written_off"},
        "paid": set(),
        "written_off": set(),
        "cancelled": set(),
    },
}
CONVERSION_READY = {
    "offer": {"accepted"},
    "order": {"confirmed", "fulfilled"},
    "delivery_note": {"final", "delivered"},
}


def _now() -> int:
    return int(time.time())


def _utc(timestamp: int | None = None) -> str:
    value = datetime.fromtimestamp(timestamp or _now(), tz=timezone.utc)
    return value.replace(microsecond=0).isoformat()


def _json(value: Mapping[str, Any] | None, *, max_bytes: int = 128 * 1024) -> str:
    encoded = json.dumps(
        dict(value or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("business document payload is too large")
    return encoded


@dataclass(frozen=True)
class BusinessDocument:
    lifecycle_id: str
    kind: str
    status: str
    external_id: str
    contact_id: str
    project_id: str
    predecessor_id: str
    title: str
    working: dict
    snapshot: dict
    document_id: str
    number: str
    correction_id: str
    created_at: int
    created_by: str
    updated_at: int
    updated_by: str
    version: int


@dataclass(frozen=True)
class FinalizationAttempt:
    attempt_id: str
    lifecycle_id: str
    invoice_id: str
    state: str
    draft_number: str
    reserved_number: str
    document_id: str
    started_at: int
    finished_at: int
    actor: str
    error: str


class FinanceLifecycleStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / ".simpleoffice-meta" / "v3-finance.sqlite3"
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
                CREATE TABLE IF NOT EXISTS v3_business_document(
                    lifecycle_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_id TEXT NOT NULL DEFAULT '',
                    contact_id TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT '',
                    predecessor_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    working_json TEXT NOT NULL DEFAULT '{}',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    document_id TEXT NOT NULL DEFAULT '',
                    number TEXT NOT NULL DEFAULT '',
                    correction_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(kind, external_id)
                );
                CREATE INDEX IF NOT EXISTS ix_v3_business_contact
                    ON v3_business_document(contact_id, updated_at DESC, lifecycle_id);
                CREATE INDEX IF NOT EXISTS ix_v3_business_predecessor
                    ON v3_business_document(predecessor_id, created_at, lifecycle_id);
                CREATE INDEX IF NOT EXISTS ix_v3_business_status
                    ON v3_business_document(kind, status, updated_at DESC, lifecycle_id);

                CREATE TABLE IF NOT EXISTS v3_business_history(
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lifecycle_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    from_status TEXT NOT NULL DEFAULT '',
                    to_status TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at INTEGER NOT NULL,
                    FOREIGN KEY(lifecycle_id) REFERENCES v3_business_document(lifecycle_id)
                );
                CREATE INDEX IF NOT EXISTS ix_v3_business_history_doc
                    ON v3_business_history(lifecycle_id, history_id);

                CREATE TABLE IF NOT EXISTS v3_invoice_finalization_attempt(
                    attempt_id TEXT PRIMARY KEY,
                    lifecycle_id TEXT NOT NULL,
                    invoice_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    draft_number TEXT NOT NULL DEFAULT '',
                    reserved_number TEXT NOT NULL DEFAULT '',
                    document_id TEXT NOT NULL DEFAULT '',
                    started_at INTEGER NOT NULL,
                    finished_at INTEGER NOT NULL DEFAULT 0,
                    actor TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(lifecycle_id) REFERENCES v3_business_document(lifecycle_id)
                );
                CREATE INDEX IF NOT EXISTS ix_v3_invoice_attempt_invoice
                    ON v3_invoice_finalization_attempt(invoice_id, started_at DESC, attempt_id);
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> BusinessDocument:
        return BusinessDocument(
            lifecycle_id=str(row["lifecycle_id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            external_id=str(row["external_id"]),
            contact_id=str(row["contact_id"]),
            project_id=str(row["project_id"]),
            predecessor_id=str(row["predecessor_id"]),
            title=str(row["title"]),
            working=json.loads(row["working_json"] or "{}"),
            snapshot=json.loads(row["snapshot_json"] or "{}"),
            document_id=str(row["document_id"]),
            number=str(row["number"]),
            correction_id=str(row["correction_id"]),
            created_at=int(row["created_at"]),
            created_by=str(row["created_by"]),
            updated_at=int(row["updated_at"]),
            updated_by=str(row["updated_by"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _attempt(row: sqlite3.Row) -> FinalizationAttempt:
        return FinalizationAttempt(
            attempt_id=str(row["attempt_id"]),
            lifecycle_id=str(row["lifecycle_id"]),
            invoice_id=str(row["invoice_id"]),
            state=str(row["state"]),
            draft_number=str(row["draft_number"]),
            reserved_number=str(row["reserved_number"]),
            document_id=str(row["document_id"]),
            started_at=int(row["started_at"]),
            finished_at=int(row["finished_at"]),
            actor=str(row["actor"]),
            error=str(row["error"]),
        )

    def _history(
        self,
        db,
        lifecycle_id: str,
        action: str,
        actor: str,
        *,
        from_status: str = "",
        to_status: str = "",
        detail: Mapping[str, Any] | None = None,
        occurred_at: int | None = None,
    ) -> None:
        db.execute(
            """INSERT INTO v3_business_history(
                lifecycle_id,action,from_status,to_status,actor,detail_json,occurred_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                lifecycle_id,
                str(action)[:100],
                str(from_status)[:40],
                str(to_status)[:40],
                str(actor)[:200],
                _json(detail, max_bytes=16 * 1024),
                occurred_at or _now(),
            ),
        )

    def get(self, lifecycle_id: str) -> BusinessDocument:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM v3_business_document WHERE lifecycle_id=?",
                (str(lifecycle_id),),
            ).fetchone()
        if row is None:
            raise LookupError("unknown business document")
        return self._record(row)

    def by_external(self, kind: str, external_id: str) -> BusinessDocument | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM v3_business_document WHERE kind=? AND external_id=?",
                (str(kind), str(external_id)),
            ).fetchone()
        return self._record(row) if row else None

    def create(
        self,
        kind: str,
        actor: str,
        *,
        contact_id: str = "",
        project_id: str = "",
        title: str = "",
        working: Mapping[str, Any] | None = None,
        predecessor_id: str = "",
        external_id: str = "",
    ) -> BusinessDocument:
        kind = str(kind).strip()
        actor = str(actor).strip()
        if kind not in KINDS or not actor:
            raise ValueError("invalid business document kind or actor")
        if predecessor_id:
            predecessor = self.get(predecessor_id)
            if kind not in SUCCESSORS.get(predecessor.kind, set()):
                raise ValueError("business document kind is not a valid successor")
        now = _now()
        lifecycle_id = uuid.uuid4().hex
        status = "draft"
        with self._db() as db:
            try:
                db.execute(
                    """INSERT INTO v3_business_document(
                        lifecycle_id,kind,status,external_id,contact_id,project_id,
                        predecessor_id,title,working_json,created_at,created_by,
                        updated_at,updated_by
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        lifecycle_id,
                        kind,
                        status,
                        str(external_id).strip()[:200],
                        str(contact_id).strip()[:200],
                        str(project_id).strip()[:200],
                        str(predecessor_id).strip(),
                        " ".join(str(title).split())[:300],
                        _json(working),
                        now,
                        actor[:200],
                        now,
                        actor[:200],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("business document already exists") from exc
            self._history(
                db,
                lifecycle_id,
                "created",
                actor,
                to_status=status,
                detail={"kind": kind, "predecessor_id": predecessor_id},
                occurred_at=now,
            )
        return self.get(lifecycle_id)

    def update_draft(
        self,
        lifecycle_id: str,
        actor: str,
        *,
        title: str | None = None,
        working: Mapping[str, Any] | None = None,
        contact_id: str | None = None,
        project_id: str | None = None,
    ) -> BusinessDocument:
        current = self.get(lifecycle_id)
        if current.status != "draft":
            raise ValueError("only a draft business document can be edited")
        now = _now()
        with self._db() as db:
            db.execute(
                """UPDATE v3_business_document SET
                    title=?,working_json=?,contact_id=?,project_id=?,
                    updated_at=?,updated_by=?,version=version+1
                   WHERE lifecycle_id=?""",
                (
                    current.title if title is None else " ".join(str(title).split())[:300],
                    _json(current.working if working is None else working),
                    current.contact_id if contact_id is None else str(contact_id).strip()[:200],
                    current.project_id if project_id is None else str(project_id).strip()[:200],
                    now,
                    str(actor)[:200],
                    lifecycle_id,
                ),
            )
            self._history(db, lifecycle_id, "draft_updated", actor, occurred_at=now)
        return self.get(lifecycle_id)

    def transition(
        self,
        lifecycle_id: str,
        to_status: str,
        actor: str,
        *,
        detail: Mapping[str, Any] | None = None,
    ) -> BusinessDocument:
        current = self.get(lifecycle_id)
        target = str(to_status).strip()
        if target not in TRANSITIONS.get(current.kind, {}).get(current.status, set()):
            raise ValueError(
                f"transition {current.kind}:{current.status}->{target} is not allowed"
            )
        snapshot = current.snapshot
        if current.kind in GENERIC_KINDS and not snapshot and target != "cancelled":
            snapshot = dict(current.working)
        now = _now()
        with self._db() as db:
            db.execute(
                """UPDATE v3_business_document SET
                    status=?,snapshot_json=?,updated_at=?,updated_by=?,version=version+1
                   WHERE lifecycle_id=?""",
                (
                    target,
                    _json(snapshot),
                    now,
                    str(actor)[:200],
                    lifecycle_id,
                ),
            )
            self._history(
                db,
                lifecycle_id,
                "transition",
                actor,
                from_status=current.status,
                to_status=target,
                detail=detail,
                occurred_at=now,
            )
        return self.get(lifecycle_id)

    def convert(
        self,
        lifecycle_id: str,
        target_kind: str,
        actor: str,
        *,
        external_id: str = "",
    ) -> BusinessDocument:
        source = self.get(lifecycle_id)
        target_kind = str(target_kind).strip()
        if target_kind not in SUCCESSORS.get(source.kind, set()):
            raise ValueError("requested successor kind is not allowed")
        if source.status not in CONVERSION_READY.get(source.kind, set()):
            raise ValueError("source document is not ready for conversion")
        frozen = dict(source.snapshot or source.working)
        if target_kind == "invoice":
            if not str(external_id).strip():
                raise ValueError("invoice conversion requires an existing invoice draft")
            from .business_document_generation import invoice
            invoice_row = invoice(self.root, str(external_id).strip())
            if invoice_row.get("status") != "draft":
                raise ValueError("invoice successor must still be a draft")
            if source.contact_id and str(invoice_row.get("contact_id", "")) != source.contact_id:
                raise ValueError("invoice successor belongs to another contact")
        title_prefix = {
            "order": "Auftrag",
            "delivery_note": "Lieferschein",
            "invoice": "Rechnung",
        }.get(target_kind, target_kind)
        created = self.create(
            target_kind,
            actor,
            contact_id=source.contact_id,
            project_id=source.project_id,
            title=f"{title_prefix}: {source.title}"[:300],
            working=frozen,
            predecessor_id=source.lifecycle_id,
            external_id=external_id,
        )
        return created

    def successors(self, lifecycle_id: str) -> list[BusinessDocument]:
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM v3_business_document
                   WHERE predecessor_id=? ORDER BY created_at,lifecycle_id""",
                (str(lifecycle_id),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list(
        self,
        *,
        kind: str = "",
        status: str = "",
        contact_id: str = "",
        limit: int = 200,
    ) -> list[BusinessDocument]:
        size = max(1, min(500, int(limit)))
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM v3_business_document
                   WHERE (?='' OR kind=?)
                     AND (?='' OR status=?)
                     AND (?='' OR contact_id=?)
                   ORDER BY updated_at DESC,lifecycle_id DESC LIMIT ?""",
                (
                    kind,
                    kind,
                    status,
                    status,
                    contact_id,
                    contact_id,
                    size,
                ),
            ).fetchall()
        return [self._record(row) for row in rows]

    def history(self, lifecycle_id: str, limit: int = 200) -> list[dict[str, Any]]:
        size = max(1, min(500, int(limit)))
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM v3_business_history
                   WHERE lifecycle_id=? ORDER BY history_id DESC LIMIT ?""",
                (str(lifecycle_id), size),
            ).fetchall()
        return [
            {
                "history_id": int(row["history_id"]),
                "action": str(row["action"]),
                "from_status": str(row["from_status"]),
                "to_status": str(row["to_status"]),
                "actor": str(row["actor"]),
                "detail": json.loads(row["detail_json"] or "{}"),
                "occurred_at": int(row["occurred_at"]),
                "occurred_at_iso": _utc(int(row["occurred_at"])),
            }
            for row in rows
        ]

    def attach_invoice(
        self,
        invoice_row: Mapping[str, Any],
        actor: str,
        *,
        predecessor_id: str = "",
    ) -> BusinessDocument:
        invoice_id = str(invoice_row.get("invoice_id", "")).strip()
        if not invoice_id:
            raise ValueError("invoice id is required")
        existing = self.by_external("invoice", invoice_id)
        if existing is None:
            existing = self.create(
                "invoice",
                actor,
                contact_id=str(invoice_row.get("contact_id", "")),
                project_id="",
                title=f"Rechnung {invoice_row.get('invoice_number', invoice_id)}",
                working={
                    "totals": invoice_row.get("totals", {}),
                    "currency": invoice_row.get("currency", ""),
                    "issue_date": invoice_row.get("issue_date", ""),
                    "due_date": invoice_row.get("due_date", ""),
                },
                predecessor_id=predecessor_id,
                external_id=invoice_id,
            )
        return existing

    def _set_invoice_state(
        self,
        lifecycle_id: str,
        status: str,
        actor: str,
        *,
        number: str = "",
        document_id: str = "",
        correction_id: str = "",
        detail: Mapping[str, Any] | None = None,
    ) -> BusinessDocument:
        current = self.get(lifecycle_id)
        now = _now()
        snapshot = current.snapshot or current.working
        with self._db() as db:
            db.execute(
                """UPDATE v3_business_document SET
                    status=?,snapshot_json=?,number=?,document_id=?,correction_id=?,
                    updated_at=?,updated_by=?,version=version+1
                   WHERE lifecycle_id=?""",
                (
                    status,
                    _json(snapshot),
                    str(number or current.number)[:200],
                    str(document_id or current.document_id)[:200],
                    str(correction_id or current.correction_id)[:200],
                    now,
                    str(actor)[:200],
                    lifecycle_id,
                ),
            )
            if current.status != status or detail:
                self._history(
                    db,
                    lifecycle_id,
                    "invoice_sync",
                    actor,
                    from_status=current.status,
                    to_status=status,
                    detail=detail,
                    occurred_at=now,
                )
        return self.get(lifecycle_id)

    def sync_invoice(self, invoice_row: Mapping[str, Any], actor: str) -> BusinessDocument:
        from .business_document_generation import invoice_state

        lifecycle = self.attach_invoice(invoice_row, actor)
        legacy_status = str(invoice_row.get("status", "draft"))
        if legacy_status == "draft":
            target = "draft" if lifecycle.status != "finalizing" else "finalizing"
        else:
            payment = invoice_state(dict(invoice_row))
            mapped = {
                "open": "final",
                "partial": "partial",
                "paid": "paid",
                "overdue": "overdue",
                "credited": "cancelled",
                "written_off": "written_off",
                "draft": "draft",
            }
            target = mapped.get(str(payment.get("status", "")), "final")
            if lifecycle.status == "sent" and target == "final":
                target = "sent"
        correction_id = ""
        if target == "cancelled":
            notes = [
                item
                for item in invoice_row.get("credit_notes", [])
                if isinstance(item, dict)
            ]
            correction_id = str(notes[-1].get("credit_note_id", "")) if notes else ""
        return self._set_invoice_state(
            lifecycle.lifecycle_id,
            target,
            actor,
            number=str(invoice_row.get("invoice_number", "")),
            document_id=str(invoice_row.get("document_id", "")),
            correction_id=correction_id,
            detail={"legacy_status": legacy_status},
        )

    def begin_finalization(
        self,
        invoice_row: Mapping[str, Any],
        actor: str,
    ) -> tuple[BusinessDocument, FinalizationAttempt]:
        lifecycle = self.attach_invoice(invoice_row, actor)
        if str(invoice_row.get("status", "")) != "draft":
            return self.sync_invoice(invoice_row, actor), self.latest_attempt(
                str(invoice_row.get("invoice_id", ""))
            )
        now = _now()
        attempt_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM v3_business_document WHERE lifecycle_id=?",
                (lifecycle.lifecycle_id,),
            ).fetchone()
            current = self._record(row)
            if current.status == "finalizing":
                raise ValueError("invoice lifecycle is already finalizing")
            if current.status not in {"draft", "failed"}:
                raise ValueError("invoice lifecycle cannot be finalized from its current state")
            db.execute(
                """UPDATE v3_business_document SET status='finalizing',
                   updated_at=?,updated_by=?,version=version+1 WHERE lifecycle_id=?""",
                (now, str(actor)[:200], current.lifecycle_id),
            )
            db.execute(
                """INSERT INTO v3_invoice_finalization_attempt(
                    attempt_id,lifecycle_id,invoice_id,state,draft_number,
                    started_at,actor
                ) VALUES(?,?,?,'finalizing',?,?,?)""",
                (
                    attempt_id,
                    current.lifecycle_id,
                    str(invoice_row.get("invoice_id", "")),
                    str(invoice_row.get("invoice_number", ""))[:200],
                    now,
                    str(actor)[:200],
                ),
            )
            self._history(
                db,
                current.lifecycle_id,
                "finalization_started",
                actor,
                from_status=current.status,
                to_status="finalizing",
                detail={"attempt_id": attempt_id},
                occurred_at=now,
            )
        return self.get(lifecycle.lifecycle_id), self.attempt(attempt_id)

    def finish_finalization(
        self,
        attempt_id: str,
        invoice_row: Mapping[str, Any],
        actor: str,
    ) -> BusinessDocument:
        attempt = self.attempt(attempt_id)
        now = _now()
        number = str(invoice_row.get("invoice_number", ""))
        document_id = str(invoice_row.get("document_id", ""))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE v3_invoice_finalization_attempt SET
                   state='succeeded',reserved_number=?,document_id=?,finished_at=?,
                   error='' WHERE attempt_id=?""",
                (number[:200], document_id[:200], now, attempt_id),
            )
        return self._set_invoice_state(
            attempt.lifecycle_id,
            "final",
            actor,
            number=number,
            document_id=document_id,
            detail={"attempt_id": attempt_id},
        )

    def fail_finalization(
        self,
        attempt_id: str,
        actor: str,
        *,
        error: str,
        reserved_number: str = "",
    ) -> BusinessDocument:
        attempt = self.attempt(attempt_id)
        now = _now()
        safe_error = " ".join(str(error).replace("\x00", " ").split())[:1000]
        with self._db() as db:
            db.execute(
                """UPDATE v3_invoice_finalization_attempt SET
                   state='failed',reserved_number=?,finished_at=?,error=?
                   WHERE attempt_id=?""",
                (str(reserved_number)[:200], now, safe_error, attempt_id),
            )
        return self._set_invoice_state(
            attempt.lifecycle_id,
            "failed",
            actor,
            detail={"attempt_id": attempt_id, "error": safe_error},
        )

    def attempt(self, attempt_id: str) -> FinalizationAttempt:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM v3_invoice_finalization_attempt WHERE attempt_id=?",
                (str(attempt_id),),
            ).fetchone()
        if row is None:
            raise LookupError("unknown finalization attempt")
        return self._attempt(row)

    def latest_attempt(self, invoice_id: str) -> FinalizationAttempt | None:
        with self._db() as db:
            row = db.execute(
                """SELECT * FROM v3_invoice_finalization_attempt
                   WHERE invoice_id=? ORDER BY started_at DESC,attempt_id DESC LIMIT 1""",
                (str(invoice_id),),
            ).fetchone()
        return self._attempt(row) if row else None

    def recover_finalization(
        self,
        invoice_row: Mapping[str, Any],
        actor: str,
        *,
        stale_seconds: int = 300,
        force: bool = False,
    ) -> BusinessDocument:
        invoice_id = str(invoice_row.get("invoice_id", ""))
        lifecycle = self.attach_invoice(invoice_row, actor)
        attempt = self.latest_attempt(invoice_id)
        if attempt is None:
            return self.sync_invoice(invoice_row, actor)
        if attempt.state == "succeeded":
            return self.sync_invoice(invoice_row, actor)
        if str(invoice_row.get("status", "")) != "draft":
            recovered = self.sync_invoice(invoice_row, actor)
            with self._db() as db:
                db.execute(
                    """UPDATE v3_invoice_finalization_attempt SET
                       state='recovered',finished_at=?,document_id=?,reserved_number=?
                       WHERE attempt_id=?""",
                    (
                        _now(),
                        str(invoice_row.get("document_id", ""))[:200],
                        str(invoice_row.get("invoice_number", ""))[:200],
                        attempt.attempt_id,
                    ),
                )
            return recovered
        age = max(0, _now() - attempt.started_at)
        if attempt.state == "finalizing" and not force and age < max(30, int(stale_seconds)):
            raise ValueError("finalization attempt is not stale")
        current = self.get(lifecycle.lifecycle_id)
        now = _now()
        with self._db() as db:
            db.execute(
                """UPDATE v3_business_document SET status='draft',
                   updated_at=?,updated_by=?,version=version+1 WHERE lifecycle_id=?""",
                (now, str(actor)[:200], current.lifecycle_id),
            )
            db.execute(
                """UPDATE v3_invoice_finalization_attempt SET
                   state='recovered',finished_at=? WHERE attempt_id=?""",
                (now, attempt.attempt_id),
            )
            self._history(
                db,
                current.lifecycle_id,
                "finalization_recovered",
                actor,
                from_status=current.status,
                to_status="draft",
                detail={"attempt_id": attempt.attempt_id, "age_seconds": age},
                occurred_at=now,
            )
        return self.get(current.lifecycle_id)


def track_invoice_best_effort(root: str | Path, invoice_row: Mapping[str, Any], actor: str) -> None:
    try:
        FinanceLifecycleStore(root).sync_invoice(invoice_row, actor)
    except (OSError, sqlite3.Error, ValueError, LookupError):
        return


def finalize_invoice_tracked(
    root: str | Path,
    invoice_id: str,
    actor: str,
):
    """Wrap the existing invoice finalizer without replacing its PDF/XML logic."""
    root = Path(root).expanduser().resolve()
    from .business_document_generation import invoice, _invoice_store_path
    from .business_documents import finalize_invoice
    from .document_store import DocumentStore, atomic_json_write
    from .file_lock import exclusive_file_lock

    before = invoice(root, invoice_id)
    if before.get("status") != "draft" and before.get("document_id"):
        FinanceLifecycleStore(root).sync_invoice(before, actor)
        return before, DocumentStore(root).get_document(before["document_id"])

    store = FinanceLifecycleStore(root)
    _lifecycle, attempt = store.begin_finalization(before, actor)
    draft_number = str(before.get("invoice_number", ""))
    try:
        row, document = finalize_invoice(root, invoice_id, actor)
    except Exception as exc:
        current = invoice(root, invoice_id)
        reserved = str(current.get("invoice_number", ""))
        if (
            current.get("status") == "draft"
            and not current.get("document_id")
            and draft_number
            and reserved != draft_number
        ):
            path = _invoice_store_path(root, invoice_id)
            with exclusive_file_lock(path.with_suffix(".lock")):
                current = invoice(root, invoice_id)
                if current.get("status") == "draft" and not current.get("document_id"):
                    current["invoice_number"] = draft_number
                    current.setdefault("history", []).append({
                        "type": "v3_finalization_number_released",
                        "at": _utc(),
                        "actor": actor,
                        "reserved_number": reserved,
                    })
                    atomic_json_write(path, current)
        store.fail_finalization(
            attempt.attempt_id,
            actor,
            error=str(exc),
            reserved_number=reserved,
        )
        raise
    except BaseException:
        # Deliberately leave the lifecycle in finalizing. A killed process cannot
        # execute cleanup either; this state is what makes crash recovery visible.
        raise
    store.finish_finalization(attempt.attempt_id, row, actor)
    return row, document
