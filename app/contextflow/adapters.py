"""Read-only, actor-scoped domain adapters for ContextFlow (Batch 1).

Every method requires a non-empty actor string and enforces the source
store's own permission checks. Relations never grant rights: invoice rows
are only readable after a contact manage check, and invoice state stays
"unknown" without a confirmed case link. No writes, no network calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..business_document_generation import invoice as _read_invoice_row
from ..calendar_store import CalendarStore
from ..contact_store import ContactStore
from ..mail_case_store import MailCaseStore
from ..site_visit_store import SiteVisitStore
from ..todo_store import TodoStore
from .contracts import (
    CALENDAR_EVENT_TYPE,
    CONTACT_TYPE,
    INVOICE_TYPE,
    INVOICE_URL,
    MAIL_CASE_TYPE,
    MAIL_CASE_URL,
    TASKS_URL,
    TASK_TYPE,
    VISIT_TYPE,
    VISIT_URL,
    CalendarEventSummary,
    ContactSummary,
    EvidenceRef,
    FindingSummary,
    InvoiceState,
    MailCaseSummary,
    TodoSummary,
    VisitSummary,
)

_SECRET_KEYS = frozenset(
    {
        "credentials",
        "credential",
        "password",
        "passwd",
        "secret",
        "secrets",
        "token",
        "api_key",
        "apikey",
        "smtp_password",
        "mail_password",
    }
)


def _require_actor(actor: object) -> str:
    """Return the actor unchanged, or reject empty/missing actors strictly."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("contextflow requires a non-empty actor")
    return actor


def _revision(*parts: object) -> str:
    """Short stable hash over ids and change markers, never over content."""
    payload = json.dumps([str(part) for part in parts], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _record_revision(row: dict[str, Any]) -> str:
    """Hash over the canonical stored record (no plaintext content out)."""
    payload = json.dumps(dict(row), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _scrub(value: Any) -> Any:
    """Remove secret-looking keys from a copied structure (defense in depth)."""
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if str(key).lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


class ContextflowReads:
    """Actor-scoped read access over existing SimpleOffice stores."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self._mail = MailCaseStore(self.root)
        self._visits = SiteVisitStore(self.root)
        self._contacts = ContactStore(self.root)
        self._todos = TodoStore(self.root)
        self._calendar = CalendarStore(self.root)

    def read_mail_case(self, actor: str, case_id: str) -> dict[str, Any]:
        """Return a mail-case summary plus evidence.

        Raises PermissionError without case read rights (unknown cases are
        also reported as PermissionError, no existence leak) and ValueError
        for empty actors or references.
        """
        actor = _require_actor(actor)
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("mail case reference is required")
        case = self._mail.get_case(actor, case_id)
        summary: MailCaseSummary = {
            "case_id": str(case.get("id", case_id)),
            "title": str(case.get("title", "")),
            "status": str(case.get("status", "")),
            "account_id": str(case.get("account_id", "")),
            "created_by": str(case.get("created_by", "")),
            "created_at": str(case.get("created_at", "")),
            "updated_at": str(case.get("updated_at", "")),
            "message_count": len(case.get("messages", []) or []),
        }
        evidence: EvidenceRef = {
            "type": MAIL_CASE_TYPE,
            "id": summary["case_id"],
            "revision": _revision(summary["case_id"], summary["updated_at"]),
            "url": MAIL_CASE_URL.format(case_id=quote(summary["case_id"], safe="")),
        }
        return {"summary": summary, "evidence": evidence}

    def read_contact(self, actor: str, contact_id: str) -> dict[str, Any]:
        """Return a contact summary plus evidence (read rights enforced)."""
        actor = _require_actor(actor)
        if not isinstance(contact_id, str) or not contact_id.strip():
            raise ValueError("contact reference is required")
        contact = self._contacts.get(contact_id, actor)
        fields = contact.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        summary: ContactSummary = {
            "contact_id": str(contact.get("contact_id", contact_id)),
            "display_name": str(fields.get("display_name", "")),
            "email": str(fields.get("email", "")),
        }
        evidence: EvidenceRef = {
            "type": CONTACT_TYPE,
            "id": summary["contact_id"],
            "revision": _revision(
                summary["contact_id"], contact.get("updated_at", "")
            ),
        }
        return {"summary": summary, "evidence": evidence}

    def read_visit(self, actor: str, visit_id: str) -> dict[str, Any]:
        """Return a visit summary with open findings (credentials stripped)."""
        actor = _require_actor(actor)
        if not isinstance(visit_id, str) or not visit_id.strip():
            raise ValueError("visit reference is required")
        record = _scrub(self._visits.get(visit_id, actor))
        findings: list[FindingSummary] = []
        for item in record.get("findings", []) or []:
            if not isinstance(item, dict):
                continue
            findings.append(
                {
                    "finding_id": str(item.get("finding_id", "")),
                    "title": str(item.get("title", "")),
                    "status": str(item.get("status", "")),
                    "evidence_class": str(item.get("evidence_class", "")),
                    "confidence": int(item.get("confidence", 0) or 0),
                    "observed_at": str(item.get("observed_at", "")),
                }
            )
        summary: VisitSummary = {
            "visit_id": str(record.get("visit_id", visit_id)),
            "title": str(record.get("title", "")),
            "customer": str(record.get("customer", "")),
            "site": str(record.get("site", "")),
            "visit_date": str(record.get("visit_date", "")),
            "status": str(record.get("status", "")),
            "created_at": str(record.get("created_at", "")),
            "updated_at": str(record.get("updated_at", "")),
            "findings": findings,
        }
        evidence: EvidenceRef = {
            "type": VISIT_TYPE,
            "id": summary["visit_id"],
            "revision": _record_revision(record),
            "url": VISIT_URL.format(visit_id=quote(summary["visit_id"], safe="")),
        }
        return {"summary": summary, "evidence": evidence}

    def list_visits(self, actor: str) -> dict[str, Any]:
        """List visit summaries visible to the actor (no detail reads)."""
        actor = _require_actor(actor)
        items = [
            {
                "visit_id": str(row.get("visit_id", "")),
                "title": str(row.get("title", "")),
                "visit_date": str(row.get("visit_date", "")),
                "status": str(row.get("status", "")),
            }
            for row in self._visits.list(actor)
            if isinstance(row, dict)
        ]
        return {"items": items}

    def read_todos(
        self, actor: str, contact_id: str = ""
    ) -> dict[str, Any]:
        """List todo summaries visible to the actor, optionally filtered."""
        actor = _require_actor(actor)
        rows = self._todos.items(actor, contact_id=contact_id or "")
        items: list[TodoSummary] = []
        evidence: list[EvidenceRef] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("id", ""))
            items.append(
                {
                    "id": item_id,
                    "title": str(row.get("title", "")),
                    "done": bool(row.get("done", False)),
                    "due": str(row.get("due") or ""),
                    "contact_id": str(row.get("contact_id") or ""),
                    "project_id": str(row.get("project_id") or ""),
                    "list_id": str(row.get("list_id", "")),
                }
            )
            evidence.append(
                {
                    "type": TASK_TYPE,
                    "id": item_id,
                    "revision": _revision(item_id, row.get("updated_at", "")),
                    "url": TASKS_URL,
                }
            )
        return {"items": items, "evidence": evidence}

    def read_calendar(
        self, actor: str, lower: datetime, upper: datetime
    ) -> dict[str, Any]:
        """List calendar events visible to the actor inside a time window."""
        actor = _require_actor(actor)
        if not isinstance(lower, datetime) or not isinstance(upper, datetime):
            raise ValueError("calendar window requires datetime bounds")
        rows = self._calendar.occurrences(actor, lower, upper)
        items: list[CalendarEventSummary] = []
        evidence: list[EvidenceRef] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_id = str(row.get("event_id", ""))
            items.append(
                {
                    "event_id": event_id,
                    "title": str(row.get("title", "")),
                    "start": str(row.get("start", "")),
                    "end": str(row.get("end", "")),
                }
            )
            evidence.append(
                {
                    "type": CALENDAR_EVENT_TYPE,
                    "id": event_id,
                    "revision": _revision(event_id, row.get("updated_at", "")),
                }
            )
        return {"items": items, "evidence": evidence}

    def read_invoice_state(
        self,
        actor: str,
        *,
        contact_id: str,
        case_link_confirmed: bool,
        invoice_id: str | None = None,
    ) -> InvoiceState:
        """Return the invoice state for one case context.

        Defaults to ``unknown``. A ``linked`` result requires all of: a
        confirmed case link, a readable contact, contact manage rights for
        the actor, a confirmed invoice reference, and a stored row whose
        contact matches. Rights are checked before and after the row read;
        a relation alone never grants access.
        """
        actor = _require_actor(actor)
        if not isinstance(contact_id, str) or not contact_id.strip():
            raise ValueError("contact reference is required")
        if case_link_confirmed is not True:
            return {"state": "unknown", "reason": "no_confirmed_case_link"}
        contact = self._contacts.get(contact_id, actor)
        if not self._contacts.can_manage_contact(contact, actor):
            raise PermissionError("invoice context requires contact manage rights")
        if not invoice_id or not str(invoice_id).strip():
            return {"state": "unknown", "reason": "no_confirmed_invoice_reference"}
        try:
            row = _read_invoice_row(self.root, str(invoice_id))
        except (KeyError, ValueError, FileNotFoundError, OSError):
            return {"state": "unknown", "reason": "not_linked"}
        if not isinstance(row, dict) or str(row.get("contact_id") or "") != contact_id:
            return {"state": "unknown", "reason": "contact_mismatch"}
        contact = self._contacts.get(contact_id, actor)
        if not self._contacts.can_manage_contact(contact, actor):
            raise PermissionError("invoice context requires contact manage rights")
        resolved_id = str(row.get("invoice_id") or invoice_id)
        return {
            "state": "linked",
            "invoice_id": resolved_id,
            "invoice_number": str(row.get("invoice_number", "")),
            "status": str(row.get("status", "")),
            "issue_date": str(row.get("issue_date", "")),
            "evidence": {
                "type": INVOICE_TYPE,
                "id": resolved_id,
                "revision": _record_revision(row),
                "url": INVOICE_URL.format(
                    invoice_id=quote(resolved_id, safe="")
                ),
            },
        }
