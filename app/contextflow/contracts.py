"""ContextFlow evidence contract (Batch 1).

Small, stable data shapes for authorized read adapters. No I/O here.
"""

from __future__ import annotations

from typing import Literal, TypedDict

Certainty = Literal["verified", "unknown"]
InvoiceStateValue = Literal["unknown", "linked"]

MAIL_CASE_TYPE = "mail_case"
VISIT_TYPE = "site_visit"
CONTACT_TYPE = "contact"
TASK_TYPE = "task"
CALENDAR_EVENT_TYPE = "calendar_event"
INVOICE_TYPE = "invoice"

MAIL_CASE_URL = "/documents/mail/reader?case={case_id}&mode=case#mail-case"
VISIT_URL = "/site-visits/{visit_id}"
INVOICE_URL = "/documents/business/invoices/{invoice_id}"
INVOICE_EDITOR_URL = "/documents/business/contacts/{contact_id}/invoice"
CUSTOMER_BILLING_URL = "/documents/business/contacts/{contact_id}/billing"
TASKS_URL = "/tasks/"

LINK_TYPE = "case_link"
FOLLOWUP_SIGNAL = "visit_follow_up"
FOLLOWUP_DAYS = 5


class CaseLinkRecord(TypedDict, total=False):
    """Persistent, actor-scoped assignment of confirmed domain objects."""

    link_id: str
    owner: str
    mail_case_id: str
    contact_id: str
    visit_id: str
    invoice_id: str
    completed_on: str
    confirmed_at: str
    confirmed_by: str
    revision: str
    status: str
    source_revisions: dict[str, str]


class HintProposal(TypedDict, total=False):
    type: str
    title: str
    approval_required: bool
    ref: str


class DeviceContext(TypedDict, total=False):
    """One visit asset referenced by pending findings (no secrets).

    Only assets that actually exist on the visit AND are referenced by at
    least one pending finding appear here. The UI may offer them as a
    visible, user-confirmed prefill — never as an automatic assignment.
    """

    asset_id: str
    name: str
    kind: str
    room_id: str
    finding_ids: list[str]
    url: str


class AssetSummary(TypedDict, total=False):
    asset_id: str
    name: str
    kind: str
    room_id: str
    status: str


class SuggestionDraft(TypedDict):
    """One validated vision suggestion (draft only, never a record)."""

    title: str
    description: str
    evidence_class: str
    confidence: int


class FollowupHint(TypedDict, total=False):
    hint_id: str
    link_id: str
    signal: str
    reason_code: str
    certainty: Certainty
    observed_at: str
    completed_on: str
    days_since_completion: int
    evidence: list[EvidenceRef]
    pending_finding_ids: list[str]
    device_context: list[DeviceContext]
    proposals: list[HintProposal]
    invoice: InvoiceState


class EvidenceRef(TypedDict, total=False):
    """Stable reference to one source object.

    ``url`` is only set for routes verified against the repository.
    ``revision`` is a short hash over id plus change markers, never content.
    """

    type: str
    id: str
    revision: str
    url: str


class CreditHintState(TypedDict, total=False):
    """Customer-credit hint for one contact (deterministic, no model calls).

    ``state`` is ``ready`` (positive balance, manage rights), ``none``
    (no positive balance) or ``hidden`` (fail closed: no rights, unknown
    contact, unreadable data). Money fields are ``Decimal``-safe strings;
    ``*_display`` variants are pre-formatted German UI text.
    """

    state: str
    reason: str
    contact_id: str
    currency: str
    balance: str
    balance_display: str
    hours: str
    hours_display: str
    rate_gross: str
    rate_display: str
    rate_basis: str
    rate_source: str
    draft_gross: str
    remaining: str
    remaining_display: str
    billing_url: str
    evidence: EvidenceRef


class MailCaseSummary(TypedDict):
    case_id: str
    title: str
    status: str
    account_id: str
    created_by: str
    created_at: str
    updated_at: str
    message_count: int


class ContactSummary(TypedDict):
    contact_id: str
    display_name: str
    email: str


class FindingSummary(TypedDict, total=False):
    finding_id: str
    title: str
    status: str
    evidence_class: str
    confidence: int
    observed_at: str
    asset_id: str
    room_id: str


class VisitSummary(TypedDict, total=False):
    visit_id: str
    title: str
    customer: str
    site: str
    visit_date: str
    status: str
    created_at: str
    updated_at: str
    findings: list[FindingSummary]
    assets: list[AssetSummary]


class TodoSummary(TypedDict, total=False):
    id: str
    title: str
    done: bool
    due: str
    contact_id: str
    project_id: str
    list_id: str


class CalendarEventSummary(TypedDict, total=False):
    event_id: str
    title: str
    start: str
    end: str


class InvoiceState(TypedDict, total=False):
    """Invoice state for one case context.

    ``unknown`` is the default: no confirmed case link, no confirmed invoice
    reference, no matching row, or a contact mismatch. ``linked`` is only
    returned after a contact manage check and a matching stored row.
    """

    state: InvoiceStateValue
    reason: str
    invoice_id: str
    invoice_number: str
    status: str
    issue_date: str
    evidence: EvidenceRef
