"""Interactive FinTS account discovery.

This module intentionally accepts the banking PIN only as a function argument.
It never writes PIN/TAN values to the database, configuration, logs or browser
storage. FinTS is optional so statement import keeps working without python-fints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .finance_schema import normalize_iban, text


class FinTSUnavailable(RuntimeError):
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
        return self.__dict__.copy()


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
    bank_code = text(getattr(account, "bank_identifier", "") or getattr(account, "blz", ""), 20)
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


def discover_accounts(connection: dict[str, Any], pin: str) -> list[dict[str, str]]:
    """Open one FinTS dialog and return the accounts visible to the login.

    The caller owns the PIN lifetime. It is deliberately not returned and the
    connection object must only contain non-secret metadata.
    """
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

    client_cls = _library()
    client = client_cls(bank_code, login_id, pin, endpoint)
    try:
        accounts = client.get_sepa_accounts()
        return [normalize_account(account).as_dict() for account in accounts]
    finally:
        # Best effort: remove the only SimpleOffice reference carrying the PIN.
        # The library may internally retain it until the client is collected.
        del client
