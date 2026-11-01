"""Synthetic fixtures on real stores under a temporary root (Batch 1).

All data is synthetic (``.test`` mail domains, ``cf-`` actors). Fixtures use
only the public store APIs; invoice rows mirror the stored JSON shape read by
``app.business_document_generation.invoice``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.business_document_generation import INVOICE_DIR
from app.calendar_store import CalendarStore
from app.contact_store import ContactStore
from app.contextflow.case_links import CaseLinkStore
from app.document_store import CONTROL_DIR
from app.mail_case_store import MailCaseStore
from app.site_visit_store import SiteVisitStore
from app.todo_store import TodoStore

OWNER = "cf-owner"
FOREIGN = "cf-foreign"

CONTACT_NAME = "Müller Service (synthetisch)"
CONTACT_EMAIL = "mueller-service@example.test"
CASE_TITLE = "Drucker defekt — Anfahrt und Befund (synthetisch)"
CASE_ACCOUNT = "cf-support@example.test"
CASE_REFERENCE = "cf-mail-0001"
VISIT_DATE = "2026-10-20T10:00"
VISIT_TITLE = "Ortstermin Drucker (synthetisch)"
FINDING_TITLE = "Papierstau, Ersatzteil offen (synthetisch)"
TODO_TITLE = "Befund nachtragen (synthetisch)"
EVENT_TITLE = "Rückfrage Kunde (synthetisch)"
EVENT_START = "2026-10-28T10:00"
EVENT_END = "2026-10-28T10:30"


def make_contact(
    root: Path,
    actor: str = OWNER,
    display_name: str = CONTACT_NAME,
    email: str = CONTACT_EMAIL,
) -> dict[str, Any]:
    """Create and return a synthetic contact."""
    return ContactStore(root).upsert(
        {"display_name": display_name, "email": email}, actor
    )


def make_mail_case(
    root: Path,
    actor: str = OWNER,
    title: str = CASE_TITLE,
    account_id: str = CASE_ACCOUNT,
    mail_reference: str = CASE_REFERENCE,
) -> str:
    """Create a synthetic mail case and return its case id."""
    return MailCaseStore(root).create_case(
        actor, title, account_id, mail_reference
    )


def make_visit(
    root: Path,
    actor: str = OWNER,
    visit_date: str = VISIT_DATE,
    finding_title: str = FINDING_TITLE,
    finding_status: str = "unreviewed",
    completed: bool = False,
) -> str:
    """Create a synthetic visit with one finding; return visit id."""
    store = SiteVisitStore(root)
    record = store.create(
        actor,
        {
            "title": VISIT_TITLE,
            "customer": CONTACT_NAME,
            "site": "Werkstatt (synthetisch)",
            "address": "Musterweg 1, 12345 Musterstadt",
            "visit_date": visit_date,
            "notes": "Anfahrt und Befund (synthetisch)",
        },
    )
    visit_id = str(record["visit_id"])
    store.add_finding(
        visit_id,
        actor,
        {
            "title": finding_title,
            "description": "Papierstau im Einzug (synthetisch)",
            "observed_at": visit_date,
            "status": finding_status,
        },
    )
    if completed:
        store.update(visit_id, actor, {"status": "completed"})
    return visit_id


def make_todo(
    root: Path,
    actor: str = OWNER,
    title: str = TODO_TITLE,
    contact_id: str = "",
) -> dict[str, Any]:
    """Create a synthetic todo and return the stored item."""
    values: dict[str, Any] = {}
    if contact_id:
        values["contact_id"] = contact_id
    return TodoStore(root).add(title, actor, values)


def make_event(
    root: Path,
    actor: str = OWNER,
    title: str = EVENT_TITLE,
    start: str = EVENT_START,
    end: str = EVENT_END,
    contact_id: str = "",
) -> dict[str, Any]:
    """Create a synthetic calendar event and return the stored row."""
    return CalendarStore(root).add(
        title, "Kundenrückfrage (synthetisch)", start, end, contact_id, actor
    )


def make_invoice_row(

    root: Path,
    contact_id: str,
    invoice_id: str,
    status: str = "draft",
    invoice_number: str = "RE-2026-001",
    issue_date: str = "2026-10-21",
) -> dict[str, Any]:
    """Store a synthetic invoice row the way the invoice reader expects it.

    Documented exception: invoice rows are written directly as stored JSON
    because no public write API exists; all other fixtures use public APIs.
    """
    row = {
        "invoice_id": invoice_id,
        "contact_id": contact_id,
        "invoice_number": invoice_number,
        "status": status,
        "issue_date": issue_date,
        "currency": "EUR",
    }
    directory = Path(root) / CONTROL_DIR / INVOICE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{invoice_id}.json"
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def confirm_link(
    root: Path,
    actor: str,
    *,
    case_id: str,
    contact_id: str,
    visit_id: str,
    completed_on: str,
    invoice_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Confirm a synthetic case link; thin wrapper over CaseLinkStore."""
    return CaseLinkStore(root).confirm(
        actor,
        mail_case_id=case_id,
        contact_id=contact_id,
        visit_id=visit_id,
        invoice_id=invoice_id,
        completed_on=completed_on,
        now=now,
    )
