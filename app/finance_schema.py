"""Validation helpers and SQLite schema for the shared finance core."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date
from typing import Any

ACCOUNT_KINDS = {"bank", "credit_card", "cash", "clearing", "internal"}
ENTRY_DIRECTIONS = {"income", "expense", "transfer", "internal"}
ENTRY_STATUSES = {"planned", "open", "partially_paid", "paid", "cancelled"}
SOURCE_TYPES = {"manual", "bank_transaction", "receipt", "invoice", "rental", "contract", "internal_allocation"}
ALLOCATION_TARGETS = {"person", "group", "cost_center", "project", "tag"}
OBLIGATION_KINDS = {"rent", "electricity", "gas", "insurance", "childcare", "subscription", "loan", "phone", "hosting", "other"}
RECURRENCE_UNITS = {"weekly", "monthly", "quarterly", "yearly"}
TAX_YEAR_STATUSES = {"collecting", "complete", "advisor", "submitted", "assessment_received", "archived"}
BANK_CONNECTION_STATUSES = {"configured", "authentication_required", "connected", "syncing", "error", "disabled"}
TAG_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{13,32}$")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def currency(value: Any) -> str:
    code = text(value or "EUR", 3).upper()
    if not _CURRENCY_RE.fullmatch(code):
        raise ValueError("currency must be a three-letter ISO code")
    return code


def iso_date(value: Any, field: str, *, allow_empty: bool = False) -> str:
    raw = text(value, 10)
    if not raw and allow_empty:
        return ""
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if not 1900 <= parsed.year <= 2200:
        raise ValueError(f"{field} is outside the supported range")
    return parsed.isoformat()


def positive_cents(value: Any, field: str = "amount") -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        cents = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be integer cents") from exc
    if cents <= 0 or cents > 99_999_999_999:
        raise ValueError(f"{field} is outside the supported range")
    return cents


def signed_cents(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("amount_cents must be a number")
    try:
        cents = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount_cents must be integer cents") from exc
    if cents == 0 or abs(cents) > 99_999_999_999:
        raise ValueError("amount_cents is outside the supported range")
    return cents


def normalize_iban(value: Any) -> str:
    iban = re.sub(r"\s+", "", str(value or "")).upper()
    if iban and not _IBAN_RE.fullmatch(iban):
        raise ValueError("IBAN is invalid")
    return iban


def transaction_fingerprint(values: dict[str, Any]) -> str:
    """Heuristic fingerprint. It must never be used as a hard uniqueness key."""
    payload = {
        "account_id": str(values.get("account_id", "")),
        "booking_date": str(values.get("booking_date", "")),
        "value_date": str(values.get("value_date", "")),
        "amount_cents": int(values.get("amount_cents", 0)),
        "currency": str(values.get("currency", "EUR")).upper(),
        "counterparty_iban": re.sub(r"\s+", "", str(values.get("counterparty_iban", ""))).upper(),
        "counterparty_name": text(values.get("counterparty_name", ""), 300).casefold(),
        "purpose": text(values.get("purpose", ""), 2000).casefold(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_account(
    account_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
    institution TEXT NOT NULL DEFAULT '', iban TEXT NOT NULL DEFAULT '',
    bic TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'EUR',
    owner TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS finance_account_owner_iban_uq
    ON finance_account(owner, iban) WHERE iban <> '';
CREATE TABLE IF NOT EXISTS finance_import_batch(
    batch_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, source_type TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '', file_sha256 TEXT NOT NULL DEFAULT '',
    period_start TEXT NOT NULL DEFAULT '', period_end TEXT NOT NULL DEFAULT '',
    imported_by TEXT NOT NULL, imported_at INTEGER NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'imported',
    FOREIGN KEY(account_id) REFERENCES finance_account(account_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS finance_import_file_uq
    ON finance_import_batch(account_id, file_sha256) WHERE file_sha256 <> '';
CREATE TABLE IF NOT EXISTS finance_bank_transaction(
    transaction_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
    import_batch_id TEXT NOT NULL DEFAULT '', booking_date TEXT NOT NULL,
    value_date TEXT NOT NULL DEFAULT '', amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR', counterparty_name TEXT NOT NULL DEFAULT '',
    counterparty_iban TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '',
    bank_transaction_id TEXT NOT NULL DEFAULT '', end_to_end_id TEXT NOT NULL DEFAULT '',
    mandate_id TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL,
    duplicate_state TEXT NOT NULL DEFAULT 'distinct', raw_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL, FOREIGN KEY(account_id) REFERENCES finance_account(account_id)
);
CREATE INDEX IF NOT EXISTS finance_tx_account_date_idx
    ON finance_bank_transaction(account_id, booking_date, transaction_id);
CREATE INDEX IF NOT EXISTS finance_tx_fingerprint_idx
    ON finance_bank_transaction(account_id, fingerprint);
CREATE INDEX IF NOT EXISTS finance_tx_bank_id_idx
    ON finance_bank_transaction(account_id, bank_transaction_id);
CREATE TABLE IF NOT EXISTS finance_entry(
    entry_id TEXT PRIMARY KEY, owner TEXT NOT NULL, account_id TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'EUR',
    booking_date TEXT NOT NULL, value_date TEXT NOT NULL DEFAULT '', description TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT 'manual',
    source_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'paid', tax_year INTEGER,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS finance_entry_owner_date_idx
    ON finance_entry(owner, booking_date, entry_id);
CREATE UNIQUE INDEX IF NOT EXISTS finance_entry_source_uq
    ON finance_entry(owner, source_type, source_id)
    WHERE source_type <> 'manual' AND source_id <> '';
CREATE TABLE IF NOT EXISTS finance_tag(
    tag_id TEXT PRIMARY KEY, owner TEXT NOT NULL, label TEXT NOT NULL COLLATE NOCASE,
    created_at INTEGER NOT NULL, UNIQUE(owner, label)
);
CREATE TABLE IF NOT EXISTS finance_entry_tag(
    entry_id TEXT NOT NULL, tag_id TEXT NOT NULL, PRIMARY KEY(entry_id, tag_id),
    FOREIGN KEY(entry_id) REFERENCES finance_entry(entry_id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES finance_tag(tag_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS finance_allocation(
    allocation_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, target_type TEXT NOT NULL,
    target_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', amount_cents INTEGER,
    share_basis_points INTEGER, created_at INTEGER NOT NULL,
    FOREIGN KEY(entry_id) REFERENCES finance_entry(entry_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS finance_allocation_entry_idx
    ON finance_allocation(entry_id, allocation_id);
CREATE TABLE IF NOT EXISTS finance_audit(
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, event TEXT NOT NULL,
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS finance_audit_entity_idx
    ON finance_audit(entity_type, entity_id, audit_id);
CREATE TABLE IF NOT EXISTS finance_obligation(
    obligation_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
    kind TEXT NOT NULL, direction TEXT NOT NULL, amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR', recurrence_unit TEXT NOT NULL,
    interval_count INTEGER NOT NULL DEFAULT 1, due_day INTEGER,
    starts_on TEXT NOT NULL, ends_on TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '', counterparty_name TEXT NOT NULL DEFAULT '',
    counterparty_iban TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
    contract_document_id TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS finance_obligation_owner_idx
    ON finance_obligation(owner, active, name COLLATE NOCASE, obligation_id);
CREATE TABLE IF NOT EXISTS finance_transaction_match(
    match_id TEXT PRIMARY KEY, owner TEXT NOT NULL, transaction_id TEXT NOT NULL,
    source_type TEXT NOT NULL, source_id TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'confirmed', note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    UNIQUE(owner, transaction_id, source_type, source_id),
    FOREIGN KEY(transaction_id) REFERENCES finance_bank_transaction(transaction_id)
);
CREATE TABLE IF NOT EXISTS finance_bank_connection(
    connection_id TEXT PRIMARY KEY, owner TEXT NOT NULL, provider TEXT NOT NULL,
    institution TEXT NOT NULL DEFAULT '', endpoint TEXT NOT NULL DEFAULT '',
    login_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'configured',
    last_successful_sync INTEGER, last_error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    UNIQUE(owner, provider, institution, login_id)
);
CREATE TABLE IF NOT EXISTS finance_tax_year(
    owner TEXT NOT NULL, tax_year INTEGER NOT NULL, status TEXT NOT NULL,
    submitted_on TEXT NOT NULL DEFAULT '', advisor_note TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL, PRIMARY KEY(owner, tax_year)
);
"""
