"""Editable mail-case drafts from evidenced ContextFlow hints (P0-E).

Only evidenced facts ever enter the draft: pending finding ids/titles
from a fresh actor-scoped visit read, the confirmed completion date,
the contact e-mail on file, and the invoice state exactly as the
authoritative adapter reports it. The draft stays editable: this flow
never requests, approves, or performs sending — the existing
MailCaseStore send path stays the only way out. Repeating the same
approval resolves atomically to the existing draft via an internal
client token; the token never appears in subject or body.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from ..mail_case_store import MailCaseStore
from .actions import ApprovalStale
from .adapters import ContextflowReads, _require_actor
from .case_links import CaseLinkStore
from .contracts import MAIL_CASE_URL
from .evaluator import FollowupEvaluator

FINALIZED_INVOICE_STATUSES = frozenset({"open"})


def _client_token(actor: str, link_id: str, expected_hint_id: str) -> str:
    """Deterministic internal idempotency token (never customer-visible)."""
    payload = "|".join(["mail-draft-v1", actor, link_id, expected_hint_id])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mail_url(case_id: str) -> str:
    from urllib.parse import quote

    return MAIL_CASE_URL.format(case_id=quote(case_id, safe=""))


def _invoice_line(invoice: dict[str, Any]) -> str:
    """One invoice sentence; only known finalized states claim creation.

    ``finalize_invoice`` is the only writer that moves an invoice row
    from ``draft`` to ``open``. Any other state — unknown, draft, empty,
    or an unexpected value — must never read as "created" to a customer.
    """
    if not isinstance(invoice, dict):
        invoice = {}
    state = str(invoice.get("state", "unknown"))
    status = str(invoice.get("status", "") or "").strip().lower()
    number = str(invoice.get("invoice_number", "") or "").strip()
    issue_date = str(invoice.get("issue_date", "") or "").strip()
    if state == "linked" and status in FINALIZED_INVOICE_STATUSES and number:
        when = f" vom {issue_date}" if issue_date else ""
        return f"Die Rechnung {number}{when} ist erstellt."
    if state == "linked" and status == "draft":
        return (
            "Die Rechnung ist erst ein Entwurf — "
            "bitte im Rechnungseditor prüfen und finalisieren."
        )
    return (
        "Zum Rechnungsstand kann dieser Entwurf keine Aussage treffen "
        "(Fall noch ungeklärt) — bitte im Rechnungseditor prüfen."
    )


def _draft_body(
    *,
    display_name: str,
    visit_title: str,
    completed_on: str,
    findings: list[tuple[str, str]],
    invoice: dict[str, Any],
) -> str:
    greeting = f"Guten Tag {display_name}," if display_name else "Guten Tag,"
    lines = [
        greeting,
        "",
        f"zum Ortstermin „{visit_title}" + (f"“ (abgeschlossen am {completed_on})" if completed_on else "“"),
        "sind aus unserer Sicht noch folgende Punkte offen:",
        "",
    ]
    for finding_id, title in findings:
        label = title or f"Befund {finding_id}"
        lines.append(f"- {label} (Befund {finding_id})")
    lines += ["", _invoice_line(invoice), "",
              "Bitte teilen Sie uns mit, welche Angaben Sie hierzu ergänzen können.",
              "",
              "Dieser Entwurf wurde aus belegten Befunden vorbereitet und ist nicht versendet."]
    return "\n".join(lines)[:20000]


def prepare_hint_draft(
    actor: str,
    link_id: str,
    expected_hint_id: str,
    approved: bool,
    now: datetime,
    root: str | Path,
) -> dict[str, Any]:
    """Prepare one editable draft for the reviewed hint, never sending it.

    Mirrors the approve_task guarantees: literal approval plus the exact
    expected hint id, freshness-checked against a new evaluation, and
    idempotent replays bound to the same approval. A replay short-circuits
    on the internal client token after the link rights check (creating
    the draft itself bumps the case revision, so re-evaluating on replay
    would always look stale). The evidence is evaluated twice on the
    create path — once to build the draft, once immediately before the
    atomic insert — so a hint that changed in between fails closed
    instead of mixing stale content with fresh facts. A stale read
    re-checks the token once before failing, so a concurrent approval
    that committed in between still resolves to the single draft.
    Every fact in the
    draft is read fresh with the actor's own rights; a missing contact
    e-mail refuses the draft instead of inventing a recipient.

    Raises ValueError for bad input or missing facts, ApprovalStale for
    changed or expired hints, and propagates PermissionError/ValueError
    when domain rights were revoked.
    """
    actor = _require_actor(actor)
    if approved is not True:
        raise ValueError("explicit approval is required")
    if not isinstance(expected_hint_id, str) or not expected_hint_id.strip():
        raise ValueError("expected hint id is required")
    if not isinstance(link_id, str) or not link_id.strip():
        raise ValueError("link reference is required")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("aware timestamp is required")

    store_root = Path(root).expanduser()
    link = CaseLinkStore(store_root).get(actor, link_id)
    case_id = str(link.get("mail_case_id", ""))
    contact_id = str(link.get("contact_id", ""))
    visit_id = str(link.get("visit_id", ""))
    completed_on = str(link.get("completed_on", ""))
    if not case_id or not contact_id or not visit_id:
        raise ValueError("link references are incomplete")

    mail = MailCaseStore(store_root)
    token = _client_token(actor, link_id, expected_hint_id.strip())

    def _replay() -> dict[str, Any] | None:
        found = mail.find_draft_by_token(actor, case_id, token)
        if found is None:
            return None
        CaseLinkStore(store_root).get(actor, link_id)
        return {
            "draft_id": found["id"],
            "created": False,
            "hint_id": expected_hint_id.strip(),
            "case_id": case_id,
            "mail_url": _mail_url(case_id),
            "subject": found["subject"],
        }

    replayed = _replay()
    if replayed is not None:
        return replayed

    evaluator = FollowupEvaluator(store_root)
    hint = evaluator.evaluate(actor, link_id, now)
    if hint is None or hint.get("hint_id") != expected_hint_id.strip():
        # A concurrent approval may have committed while we evaluated.
        replayed = _replay()
        if replayed is not None:
            return replayed
        raise ApprovalStale(
            "hint is no longer current" if hint is None
            else "hint changed since review"
        )
    pending = [str(item) for item in hint.get("pending_finding_ids", []) or []]
    if not pending:
        raise ApprovalStale("no pending findings left")

    reads = ContextflowReads(store_root)
    contact = reads.read_contact(actor, contact_id)["summary"]
    email = str(contact.get("email", "") or "").strip()
    if not email or "\n" in email or "\r" in email:
        raise ValueError("contact has no usable email address; recipient is required")
    visit = reads.read_visit(actor, visit_id)["summary"]
    titles = {
        str(item.get("finding_id", "")): str(item.get("title", ""))
        for item in visit.get("findings", []) or []
        if isinstance(item, dict) and item.get("finding_id")
    }
    findings = [(finding_id, titles.get(finding_id, "")) for finding_id in pending]
    visit_title = str(visit.get("title", "") or "").strip()
    subject = (
        f"Nachfrage zu {visit_title} ({completed_on})"
        if visit_title else "Nachfrage zu Ihrem Ortstermin"
    )[:200]
    invoice = hint.get("invoice", {})
    body = _draft_body(
        display_name=str(contact.get("display_name", "") or "").strip(),
        visit_title=visit_title or "Ortstermin",
        completed_on=completed_on,
        findings=findings,
        invoice=invoice if isinstance(invoice, dict) else {},
    )
    rechecked = evaluator.evaluate(actor, link_id, now)
    if rechecked is None or rechecked.get("hint_id") != expected_hint_id.strip():
        replayed = _replay()
        if replayed is not None:
            return replayed
        raise ApprovalStale("hint changed since review")
    draft_id, created = mail.create_draft_idempotent(
        actor, case_id, email, subject, body, client_token=token
    )
    return {
        "draft_id": draft_id,
        "created": created,
        "hint_id": expected_hint_id.strip(),
        "case_id": case_id,
        "mail_url": _mail_url(case_id),
        "subject": subject,
    }
