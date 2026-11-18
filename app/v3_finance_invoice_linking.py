"""Persistence helpers for linking existing invoice drafts into Finance 3.0."""
from __future__ import annotations

from typing import Any, Mapping
import uuid

from .v3_finance import (
    CONVERSION_READY,
    SUCCESSORS,
    BusinessDocument,
    _json,
    _now,
)


def attach_invoice_successor(
    store: Any,
    invoice_row: Mapping[str, Any],
    actor: str,
    predecessor_id: str,
) -> BusinessDocument:
    """Atomically attach a validated invoice draft to its ready source."""
    invoice_id = str(invoice_row.get("invoice_id", "")).strip()
    contact_id = str(invoice_row.get("contact_id", "")).strip()
    title = f"Rechnung {invoice_row.get('invoice_number', invoice_id)}"[:300]
    working = {
        "totals": invoice_row.get("totals", {}),
        "currency": invoice_row.get("currency", ""),
        "issue_date": invoice_row.get("issue_date", ""),
        "due_date": invoice_row.get("due_date", ""),
    }
    now = _now()
    with store._db() as db:
        db.execute("BEGIN IMMEDIATE")
        source = db.execute(
            "SELECT * FROM v3_business_document WHERE lifecycle_id=?",
            (str(predecessor_id),),
        ).fetchone()
        if source is None:
            raise ValueError("source document does not exist")
        source_row = store._record(source)
        if "invoice" not in SUCCESSORS.get(source_row.kind, set()):
            raise ValueError("invoice is not a valid successor for this document")
        if source_row.status not in CONVERSION_READY.get(source_row.kind, set()):
            raise ValueError("source document is not ready for conversion")
        if not source_row.contact_id or source_row.contact_id != contact_id:
            raise ValueError("invoice successor belongs to another contact")
        existing = db.execute(
            "SELECT * FROM v3_business_document WHERE kind='invoice' AND external_id=?",
            (invoice_id,),
        ).fetchone()
        if existing is None:
            lifecycle_id = _create_successor(
                store, db, invoice_id, contact_id, title, working,
                source_row, predecessor_id, actor, now,
            )
        else:
            lifecycle_id = _link_registered_draft(
                store, db, existing, working, source_row, predecessor_id, actor, now,
            )
    return store.get(lifecycle_id)


def _create_successor(
    store: Any,
    db: Any,
    invoice_id: str,
    contact_id: str,
    title: str,
    working: dict[str, Any],
    source_row: BusinessDocument,
    predecessor_id: str,
    actor: str,
    now: int,
) -> str:
    lifecycle_id = uuid.uuid4().hex
    successor_working = {
        **working,
        "source_snapshot": dict(source_row.snapshot or source_row.working),
    }
    db.execute(
        """INSERT INTO v3_business_document(
            lifecycle_id,kind,status,external_id,contact_id,project_id,
            predecessor_id,title,working_json,created_at,created_by,
            updated_at,updated_by
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            lifecycle_id,
            "invoice",
            "draft",
            invoice_id,
            contact_id,
            source_row.project_id,
            predecessor_id,
            title,
            _json(successor_working),
            now,
            str(actor)[:200],
            now,
            str(actor)[:200],
        ),
    )
    store._history(
        db,
        lifecycle_id,
        "created",
        actor,
        to_status="draft",
        detail={"kind": "invoice", "predecessor_id": predecessor_id},
        occurred_at=now,
    )
    return lifecycle_id


def _link_registered_draft(
    store: Any,
    db: Any,
    existing: Any,
    working: dict[str, Any],
    source_row: BusinessDocument,
    predecessor_id: str,
    actor: str,
    now: int,
) -> str:
    linked = store._record(existing)
    if linked.contact_id != source_row.contact_id or linked.status != "draft":
        raise ValueError("invoice successor is no longer an available draft")
    if linked.predecessor_id == predecessor_id:
        return linked.lifecycle_id
    if linked.predecessor_id:
        raise ValueError("invoice draft is already linked to another document")
    if linked.project_id and source_row.project_id and linked.project_id != source_row.project_id:
        raise ValueError("invoice draft is assigned to another project")
    source_snapshot = dict(source_row.snapshot or source_row.working)
    successor_working = {
        **linked.working,
        **working,
        "source_snapshot": source_snapshot,
    }
    successor_snapshot = {
        **(linked.snapshot or linked.working),
        **working,
        "source_snapshot": source_snapshot,
    }
    db.execute(
        """UPDATE v3_business_document SET predecessor_id=?,updated_at=?,
           updated_by=?,version=version+1,project_id=CASE WHEN project_id='' THEN ? ELSE project_id END,
           working_json=?,snapshot_json=? WHERE lifecycle_id=?
           AND predecessor_id='' AND status='draft'""",
        (
            predecessor_id,
            now,
            str(actor)[:200],
            source_row.project_id,
            _json(successor_working),
            _json(successor_snapshot),
            linked.lifecycle_id,
        ),
    )
    if db.execute("SELECT changes()").fetchone()[0] != 1:
        raise ValueError("invoice draft is no longer available")
    store._history(
        db,
        linked.lifecycle_id,
        "linked_as_successor",
        actor,
        from_status="draft",
        to_status="draft",
        detail={"predecessor_id": predecessor_id},
        occurred_at=now,
    )
    return linked.lifecycle_id
