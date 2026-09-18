"""Interactive FinTS account discovery and read-only transaction import.

PIN/TAN values are request-local secrets. This module never persists or returns
those values. The adapter is deliberately read-only: no transfer methods exist.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

from .finance_schema import normalize_iban, text, transaction_fingerprint


class FinTSUnavailable(RuntimeError):
    pass


class FinTSAuthenticationRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveredBankAccount:
    remote_account_id: str
    iban: str
    bic: str
    account_number: str
    subaccount: str
    bank_code: str
    currency: str
    account_type: str

    def as_dict(self) -> dict[str, str]:
        return {
            "remote_account_id": self.remote_account_id,
            "iban": self.iban,
            "bic": self.bic,
            "account_number": self.account_number,
            "subaccount": self.subaccount,
            "bank_code": self.bank_code,
            "currency": self.currency,
            "account_type": self.account_type,
        }


def _library():
    try:
        from fints.client import FinTS3PinTanClient
    except ImportError as exc:
        raise FinTSUnavailable(
            "FinTS-Unterstützung ist nicht installiert. Installiere SimpleOffice mit dem Extra 'banking'."
        ) from exc
    return FinTS3PinTanClient


def _remote_id(account: Any) -> str:
    iban = normalize_iban(getattr(account, "iban", "") or "")
    if iban:
        return f"iban:{iban}"
    bank_identifier = getattr(account, "bank_identifier", "")
    bank_code = text(getattr(bank_identifier, "bank_code", "") or bank_identifier or getattr(account, "blz", ""), 20)
    number = text(getattr(account, "accountnumber", "") or getattr(account, "account_number", ""), 80)
    sub = text(getattr(account, "subaccount", "") or getattr(account, "subaccount_number", ""), 40)
    if not number:
        raise ValueError("Bankkonto besitzt weder IBAN noch Kontonummer")
    return f"account:{bank_code}:{number}:{sub}"


def normalize_account(account: Any) -> DiscoveredBankAccount:
    bank_identifier = getattr(account, "bank_identifier", "")
    if hasattr(bank_identifier, "bank_code"):
        bank_code = text(bank_identifier.bank_code, 20)
    else:
        bank_code = text(bank_identifier or getattr(account, "blz", ""), 20)
    return DiscoveredBankAccount(
        remote_account_id=_remote_id(account),
        iban=normalize_iban(getattr(account, "iban", "") or ""),
        bic=text(getattr(account, "bic", ""), 20).upper(),
        account_number=text(getattr(account, "accountnumber", "") or getattr(account, "account_number", ""), 80),
        subaccount=text(getattr(account, "subaccount", "") or getattr(account, "subaccount_number", ""), 40),
        bank_code=bank_code,
        currency=text(getattr(account, "currency", "EUR"), 3).upper() or "EUR",
        account_type=text(getattr(account, "account_type", "") or getattr(account, "type", ""), 100),
    )


def _client(connection: dict[str, Any], pin: str):
    if text(connection.get("provider"), 30).casefold() != "fints":
        raise ValueError("Bankverbindung ist keine FinTS-Verbindung")
    bank_code = text(connection.get("bank_code"), 20)
    login_id = text(connection.get("login_id"), 200)
    endpoint = text(connection.get("endpoint"), 500)
    pin = str(pin or "")
    if not bank_code or not login_id or not endpoint:
        raise ValueError("BLZ, Benutzerkennung und FinTS-URL werden benötigt")
    if not pin:
        raise ValueError("PIN wird nur für diesen Abruf benötigt")
    if not endpoint.lower().startswith("https://"):
        raise ValueError("FinTS-URL muss HTTPS verwenden")
    return _library()(bank_code, login_id, pin, endpoint)


def discover_accounts(connection: dict[str, Any], pin: str) -> list[dict[str, str]]:
    client = _client(connection, pin)
    try:
        accounts = client.get_sepa_accounts()
        return [normalize_account(account).as_dict() for account in accounts]
    finally:
        del client


def _as_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = text(value, 32)
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return ""


def _money_cents(value: Any) -> int:
    if hasattr(value, "amount"):
        value = getattr(value, "amount")
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cents = int(amount * 100)
    if cents == 0:
        raise ValueError("FinTS-Umsatz mit Betrag 0 wird nicht unterstützt")
    return cents


def _field(source: Any, *names: str) -> Any:
    for name in names:
        if isinstance(source, dict) and source.get(name) not in {None, ""}:
            return source.get(name)
        value = getattr(source, name, None)
        if value not in {None, ""}:
            return value
    data = getattr(source, "data", None)
    if isinstance(data, dict):
        for name in names:
            if data.get(name) not in {None, ""}:
                return data.get(name)
    return ""


def normalize_transaction(transaction: Any) -> dict[str, Any]:
    """Normalize common python-fints/MT940 transaction shapes."""
    amount_raw = _field(transaction, "amount", "transaction_amount")
    amount_cents = _money_cents(amount_raw)
    status = text(_field(transaction, "status"), 40).casefold()
    if status in {"debit", "d", "dbit"} and amount_cents > 0:
        amount_cents = -amount_cents
    elif status in {"credit", "c", "crdt"} and amount_cents < 0:
        amount_cents = abs(amount_cents)

    purpose_parts = []
    for key in ("purpose", "purpose_code", "applicant_name", "additional_purpose"):
        value = _field(transaction, key)
        if value:
            purpose_parts.append(str(value))
    booking_date = _as_date(_field(transaction, "date", "booking_date", "booked_at"))
    if not booking_date:
        raise ValueError("FinTS-Umsatz ohne Buchungsdatum")
    value_date = _as_date(_field(transaction, "entry_date", "value_date", "valuta"))
    bank_id = text(_field(transaction, "transaction_id", "bank_transaction_id", "customer_reference", "bank_reference"), 300)
    counterparty_iban = ""
    raw_iban = _field(transaction, "applicant_iban", "counterparty_iban", "iban")
    if raw_iban:
        try:
            counterparty_iban = normalize_iban(raw_iban)
        except ValueError:
            counterparty_iban = ""
    return {
        "booking_date": booking_date,
        "value_date": value_date,
        "amount_cents": amount_cents,
        "currency": text(_field(transaction, "currency"), 3).upper() or "EUR",
        "counterparty_name": text(_field(transaction, "applicant_name", "counterparty_name", "payee"), 300),
        "counterparty_iban": counterparty_iban,
        "purpose": text(" ".join(purpose_parts), 2000),
        "bank_transaction_id": bank_id,
        "end_to_end_id": text(_field(transaction, "end_to_end_reference", "end_to_end_id"), 300),
        "mandate_id": text(_field(transaction, "mandate_reference", "mandate_id"), 300),
        "raw": {
            "source": "fints",
            "status": status,
            "customer_reference": text(_field(transaction, "customer_reference"), 300),
            "bank_reference": text(_field(transaction, "bank_reference"), 300),
        },
    }


def _find_remote_account(client: Any, remote_account_id: str) -> Any:
    accounts = client.get_sepa_accounts()
    for account in accounts:
        if _remote_id(account) == remote_account_id:
            return account
    raise ValueError("Das zugeordnete Bankkonto wurde beim FinTS-Abruf nicht mehr gefunden")


def fetch_transactions(
    connection: dict[str, Any],
    remote_account_id: str,
    pin: str,
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Read booked transactions for one mapped account.

    Some banks/python-fints versions can return an interactive TAN response for
    protected operations. We refuse to persist dialog state or TAN material and
    report that interactive authentication is required instead.
    """
    if end_date < start_date:
        raise ValueError("Abrufende liegt vor Abrufbeginn")
    if (end_date - start_date).days > 370:
        raise ValueError("Ein einzelner FinTS-Abruf ist auf 370 Tage begrenzt")
    client = _client(connection, pin)
    try:
        account = _find_remote_account(client, remote_account_id)
        result = client.get_transactions(account, start_date, end_date)
        if hasattr(result, "challenge") or result.__class__.__name__.casefold().startswith("needtan"):
            raise FinTSAuthenticationRequired("Die Bank verlangt für diesen Abruf eine TAN/App-Freigabe.")
        if result is None:
            return []
        if not isinstance(result, Iterable) or isinstance(result, (str, bytes, dict)):
            raise ValueError("FinTS lieferte ein unbekanntes Umsatzformat")
        return [normalize_transaction(item) for item in result]
    finally:
        del client


def default_fetch_window(last_booking_date: str | None, *, today: date | None = None, lookback_days: int = 7) -> tuple[date, date]:
    end = today or date.today()
    if last_booking_date:
        try:
            latest = date.fromisoformat(last_booking_date)
        except ValueError:
            latest = end - timedelta(days=90)
        start = latest - timedelta(days=max(1, min(int(lookback_days), 31)))
    else:
        start = end - timedelta(days=90)
    return start, end


def fingerprint_for_account(account_id: str, transaction: dict[str, Any]) -> str:
    probe = dict(transaction)
    probe["account_id"] = account_id
    return transaction_fingerprint(probe)
