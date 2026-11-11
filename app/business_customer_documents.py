"""Resolve customer-specific document defaults without bloating route handlers."""
from __future__ import annotations

from typing import Any, Mapping

from .business_document_preferences import (
    company_bank_accounts,
    document_preferences,
    selected_company_bank_account,
)


def preferred_template_id(
    crm: Mapping[str, Any],
    document_type: str,
    choices: list[dict[str, Any]],
    *,
    explicit_id: str = "",
    existing_id: str = "",
) -> str:
    explicit = str(explicit_id or "").strip()
    valid = {str(row.get("template_id", "")) for row in choices}
    if explicit:
        if explicit not in valid:
            raise ValueError("unknown business document template")
        return explicit
    existing = str(existing_id or "").strip()
    if existing and existing in valid:
        return existing
    prefs = document_preferences(crm)
    key = "letter_template_id" if document_type == "letter" else "invoice_template_id"
    preferred = prefs.get(key, "")
    if preferred and preferred in valid:
        return preferred
    active = next((str(row.get("template_id", "")) for row in choices if row.get("active")), "")
    return active or (next(iter(valid)) if valid else "")


def preferred_bank_account_id(
    settings: Mapping[str, Any],
    crm: Mapping[str, Any],
    *,
    explicit_id: str = "",
    existing_id: str = "",
) -> str:
    accounts = company_bank_accounts(settings)
    valid = {str(row.get("account_id", "")) for row in accounts}
    explicit = str(explicit_id or "").strip().lower()
    if explicit:
        if explicit not in valid:
            raise ValueError("unknown company bank account")
        return explicit
    existing = str(existing_id or "").strip().lower()
    if existing and existing in valid:
        return existing
    preferred = document_preferences(crm).get("invoice_bank_account_id", "")
    if preferred and preferred in valid:
        return preferred
    default = selected_company_bank_account(settings)
    return str(default.get("account_id", "")) if default else ""


def invoice_seller_snapshot(
    settings: Mapping[str, Any],
    account_id: str,
) -> dict[str, str]:
    account = selected_company_bank_account(settings, account_id)
    return {
        "name": str(settings.get("seller_name", "")),
        "street": str(settings.get("seller_street", "")),
        "postal": str(settings.get("seller_postal", "")),
        "city": str(settings.get("seller_city", "")),
        "country": str(settings.get("seller_country") or "DE"),
        "email": str(settings.get("seller_email", "")),
        "vat_id": str(settings.get("seller_vat_id", "")),
        "tax_number": str(settings.get("seller_tax_number", "")),
        "bank_account_id": str(account.get("account_id", "")) if account else "",
        "bank_account_label": str(account.get("label", "")) if account else "",
        "bank_holder": str(account.get("holder", "")) if account else "",
        "iban": str(account.get("iban", settings.get("seller_iban", ""))) if account else str(settings.get("seller_iban", "")),
        "blz": str(account.get("blz", settings.get("seller_blz", ""))) if account else str(settings.get("seller_blz", "")),
        "bic": str(account.get("bic", settings.get("seller_bic", ""))) if account else str(settings.get("seller_bic", "")),
        "bank": str(account.get("bank", settings.get("seller_bank", ""))) if account else str(settings.get("seller_bank", "")),
    }


def letter_customer_context(contact: Mapping[str, Any], crm: Mapping[str, Any]) -> dict[str, str]:
    fields = contact.get("fields", {}) if isinstance(contact.get("fields"), Mapping) else {}
    return {
        "customer_number": str(crm.get("customer_number", "")),
        "email": str(fields.get("email", "")),
        "phone": str(fields.get("phone", "")),
        "vat_id": str(crm.get("vat_id", "")),
        "tax_number": str(crm.get("tax_number", "")),
        "payment_terms": str(crm.get("payment_terms", "")),
        "currency": str(crm.get("currency", "")),
    }
