"""Company bank accounts and customer-specific business-document preferences."""
from __future__ import annotations

import re
import uuid
from typing import Any, Mapping

MAX_COMPANY_BANK_ACCOUNTS = 20
ACCOUNT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}$")
BIC_RE = re.compile(r"^[A-Z0-9]{8}([A-Z0-9]{3})?$")


def _clean_iban(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _clean_bic(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _clean_currency(value: Any) -> str:
    currency = str(value or "EUR").strip().upper()
    return currency if re.fullmatch(r"[A-Z]{3}", currency) else "EUR"


def normalize_company_bank_accounts(
    rows: Any,
    *,
    default_account_id: str = "",
) -> list[dict[str, str | bool]]:
    if rows in (None, ""):
        return []
    if not isinstance(rows, list):
        raise ValueError("company bank accounts must be a list")
    if len(rows) > MAX_COMPANY_BANK_ACCOUNTS:
        raise ValueError("too many company bank accounts")

    result: list[dict[str, str | bool]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError("company bank account must be an object")
        account_id = str(raw.get("account_id", "")).strip().lower()
        if not ACCOUNT_ID_RE.fullmatch(account_id):
            account_id = uuid.uuid4().hex
        if account_id in seen_ids:
            raise ValueError("company bank account identifiers must be unique")
        seen_ids.add(account_id)

        label = str(raw.get("label", "")).strip()[:120]
        holder = str(raw.get("holder", "")).strip()[:160]
        iban = _clean_iban(raw.get("iban"))
        blz = re.sub(r"\s+", "", str(raw.get("blz", "")).strip())[:20]
        bic = _clean_bic(raw.get("bic"))
        bank = str(raw.get("bank", "")).strip()[:160]
        currency = _clean_currency(raw.get("currency"))

        if not label:
            label = f"Konto {index + 1}"
        if not iban:
            raise ValueError(f"IBAN fehlt bei {label}")
        if not IBAN_RE.fullmatch(iban):
            raise ValueError(f"IBAN ist ungültig bei {label}")
        if bic and not BIC_RE.fullmatch(bic):
            raise ValueError(f"BIC ist ungültig bei {label}")

        result.append({
            "account_id": account_id,
            "label": label,
            "holder": holder,
            "iban": iban,
            "blz": blz,
            "bic": bic,
            "bank": bank,
            "currency": currency,
            "default": False,
        })

    requested = str(default_account_id or "").strip().lower()
    default_index = next(
        (idx for idx, row in enumerate(result) if row["account_id"] == requested),
        0 if result else -1,
    )
    if default_index >= 0:
        result[default_index]["default"] = True
    return result


def company_bank_accounts(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = settings.get("seller_bank_accounts")
    if isinstance(raw, list) and raw:
        requested = next(
            (
                str(row.get("account_id", ""))
                for row in raw
                if isinstance(row, Mapping) and row.get("default")
            ),
            "",
        )
        return normalize_company_bank_accounts(raw, default_account_id=requested)

    iban = _clean_iban(settings.get("seller_iban"))
    if not iban:
        return []
    return normalize_company_bank_accounts(
        [{
            "account_id": "0" * 32,
            "label": "Standardkonto",
            "holder": settings.get("seller_name", ""),
            "iban": iban,
            "blz": settings.get("seller_blz", ""),
            "bic": settings.get("seller_bic", ""),
            "bank": settings.get("seller_bank", ""),
            "currency": settings.get("currency", "EUR"),
        }],
        default_account_id="0" * 32,
    )


def selected_company_bank_account(
    settings: Mapping[str, Any],
    account_id: str = "",
) -> dict[str, Any] | None:
    rows = company_bank_accounts(settings)
    if not rows:
        return None
    requested = str(account_id or "").strip().lower()
    if requested:
        match = next((row for row in rows if row["account_id"] == requested), None)
        if match is not None:
            return dict(match)
    return dict(next((row for row in rows if row.get("default")), rows[0]))


def bank_accounts_from_form(form, current: Mapping[str, Any]) -> list[dict[str, Any]]:
    getter = getattr(form, "getlist", None)
    if getter is None:
        raw = form.get("seller_bank_accounts")
        if raw is None:
            return company_bank_accounts(current)
        return normalize_company_bank_accounts(
            raw,
            default_account_id=str(form.get("default_bank_account_id", "")),
        )

    ids = getter("bank_account_id")
    labels = getter("bank_account_label")
    holders = getter("bank_account_holder")
    ibans = getter("bank_account_iban")
    blzs = getter("bank_account_blz")
    bics = getter("bank_account_bic")
    banks = getter("bank_account_bank")
    currencies = getter("bank_account_currency")
    count = max(len(ids), len(labels), len(holders), len(ibans), len(blzs), len(bics), len(banks), len(currencies), 0)
    rows = []
    for index in range(count):
        value = lambda items: items[index] if index < len(items) else ""
        if not any(str(value(items)).strip() for items in (labels, holders, ibans, blzs, bics, banks)):
            continue
        rows.append({
            "account_id": value(ids),
            "label": value(labels),
            "holder": value(holders),
            "iban": value(ibans),
            "blz": value(blzs),
            "bic": value(bics),
            "bank": value(banks),
            "currency": value(currencies) or current.get("currency", "EUR"),
        })
    return normalize_company_bank_accounts(
        rows,
        default_account_id=str(form.get("default_bank_account_id", "")),
    )


def apply_default_bank_legacy_fields(settings: dict[str, Any]) -> None:
    account = selected_company_bank_account(settings)
    if account is None:
        return
    settings["seller_iban"] = str(account.get("iban", ""))
    settings["seller_blz"] = str(account.get("blz", ""))
    settings["seller_bic"] = str(account.get("bic", ""))
    settings["seller_bank"] = str(account.get("bank", ""))


def document_preferences(crm: Mapping[str, Any]) -> dict[str, str]:
    raw = crm.get("document_preferences", {})
    if not isinstance(raw, Mapping):
        raw = {}
    return {
        "invoice_template_id": str(raw.get("invoice_template_id", "")).strip(),
        "letter_template_id": str(raw.get("letter_template_id", "")).strip(),
        "invoice_bank_account_id": str(raw.get("invoice_bank_account_id", "")).strip().lower(),
    }


def document_preferences_from_form(form, current: Mapping[str, Any]) -> dict[str, str]:
    existing = document_preferences(current)
    return {
        "invoice_template_id": str(form.get("invoice_template_id", existing["invoice_template_id"])).strip(),
        "letter_template_id": str(form.get("letter_template_id", existing["letter_template_id"])).strip(),
        "invoice_bank_account_id": str(form.get("invoice_bank_account_id", existing["invoice_bank_account_id"])).strip().lower(),
    }


def validate_document_preferences(
    preferences: Mapping[str, Any],
    *,
    invoice_template_ids: set[str],
    letter_template_ids: set[str],
    bank_account_ids: set[str],
) -> dict[str, str]:
    clean = document_preferences({"document_preferences": preferences})
    checks = (
        ("invoice_template_id", invoice_template_ids, "invoice template"),
        ("letter_template_id", letter_template_ids, "letter template"),
        ("invoice_bank_account_id", bank_account_ids, "company bank account"),
    )
    for key, allowed, label in checks:
        value = clean[key]
        if value and value not in allowed:
            raise ValueError(f"unknown {label}")
    return clean
