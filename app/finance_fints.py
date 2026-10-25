"""Interactive FinTS account discovery and read-only transaction import.

PIN/TAN values are request-local secrets. This module never persists or returns
those values. The adapter is deliberately read-only: no transfer methods exist.
"""
from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Any, Iterable

from .finance_schema import normalize_iban, text, transaction_fingerprint
from .finance_fints_retry import run_read_with_bounded_retry


class FinTSUnavailable(RuntimeError):
    pass


class FinTSAuthenticationRequired(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        challenge_state: dict[str, Any] | None = None,
        challenge_response: Any = None,
        challenge_text: str = "",
        decoupled: bool = False,
    ):
        super().__init__(message)
        self.challenge_state = challenge_state
        self.challenge_response = challenge_response
        self.challenge_text = text(challenge_text, 2000)
        self.decoupled = decoupled is True


class FinTSFeatureUnavailable(ValueError):
    """The bank explicitly reports that a supported read operation is unavailable."""


_MAX_SCA_STATE_BYTES = 120 * 1024
_MAX_CAPABILITY_ACCOUNTS = 1000
_MAX_TAN_MECHANISMS = 100


def _encode_sca_state(value: bytes) -> str:
    if not isinstance(value, bytes) or not value or len(value) > _MAX_SCA_STATE_BYTES:
        raise ValueError("FinTS-SCA-Zustand ist ungültig oder zu groß")
    return base64.b64encode(value).decode("ascii")


def _decode_sca_state(value: Any) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_SCA_STATE_BYTES * 2:
        raise ValueError("FinTS-SCA-Sitzung ist ungültig; bitte erneut starten")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("FinTS-SCA-Sitzung ist ungültig; bitte erneut starten") from exc
    if not decoded or len(decoded) > _MAX_SCA_STATE_BYTES:
        raise ValueError("FinTS-SCA-Sitzung ist ungültig; bitte erneut starten")
    return decoded


def _restore_sca_retry_response(challenge_data: str, *, decoupled: bool):
    if not isinstance(decoupled, bool):
        raise ValueError("FinTS-SCA-Sitzung enthält einen ungültigen TAN-Modus")
    try:
        from fints.client import NeedRetryResponse
    except ImportError as exc:
        raise ValueError("FinTS-SCA-Sitzung kann mit der installierten Bibliothek nicht fortgesetzt werden") from exc
    response = NeedRetryResponse.from_data(_decode_sca_state(challenge_data))
    if not is_tan_response(response):
        raise ValueError("FinTS-SCA-Sitzung enthält keine TAN-Herausforderung")
    # python-fints omits this flag from retry serialization; restore only from
    # the authenticated, encrypted server-side challenge state.
    response.decoupled = decoupled
    return response


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


def _client(
    connection: dict[str, Any],
    pin: str,
    *,
    from_data: bytes | None = None,
):
    if text(connection.get("provider"), 30).casefold() != "fints":
        raise ValueError("Bankverbindung ist keine FinTS-Verbindung")
    if text(connection.get("status", "configured"), 40).casefold() == "disabled":
        raise ValueError("FinTS-Bankverbindung ist deaktiviert")
    bank_code = text(connection.get("bank_code"), 20)
    login_id = text(connection.get("login_id"), 200)
    endpoint = text(connection.get("endpoint"), 500)
    pin = str(pin or "")
    if not bank_code or not login_id or not endpoint:
        raise ValueError("BLZ, Benutzerkennung und FinTS-URL werden benötigt")
    if not pin:
        raise ValueError("PIN wird nur für diesen Abruf benötigt")
    if len(pin) > 256 or "\x00" in pin:
        raise ValueError("PIN-Eingabe ist ungültig")
    if not endpoint.lower().startswith("https://"):
        raise ValueError("FinTS-URL muss HTTPS verwenden")
    client_class = _library()
    product_id = os.environ.get("SIMPLEOFFICE_FINTS_PRODUCT_ID", "").strip()
    if (
        not product_id
        or len(product_id) > 64
        or not product_id.isascii()
        or any(ord(character) < 32 or ord(character) == 127 for character in product_id)
    ):
        raise FinTSUnavailable(
            "FinTS-Produkt-ID fehlt oder ist ungültig. Hinterlege eine gültige, bei der "
            "Deutschen Kreditwirtschaft registrierte Produkt-ID in SIMPLEOFFICE_FINTS_PRODUCT_ID."
        )
    options = {"product_id": product_id}
    if from_data is not None:
        if not isinstance(from_data, bytes) or not from_data or len(from_data) > 512 * 1024:
            raise ValueError("FinTS-Clientzustand ist ungültig")
        options["from_data"] = from_data
    return client_class(bank_code, login_id, pin, endpoint, **options)


def is_tan_response(value: Any) -> bool:
    """Recognize python-fints interactive challenge objects without requiring the extra."""
    if value is None:
        return False
    return value.__class__.__name__.casefold().startswith("needtan") and callable(
        getattr(value, "get_data", None)
    )


def _sepa_accounts(client: Any) -> Iterable[Any]:
    accounts = client.get_sepa_accounts()
    if is_tan_response(accounts):
        raise FinTSAuthenticationRequired(
            "Die Bank verlangt eine TAN/App-Freigabe für den Kontenabruf.",
            challenge_response=accounts,
        )
    if not isinstance(accounts, Iterable) or isinstance(accounts, (str, bytes, dict)):
        raise ValueError("FinTS lieferte ein unbekanntes Kontenformat")
    return accounts


_READ_OPERATION_NAMES = {
    "balance": ("GET_BALANCE",),
    "transactions": ("GET_TRANSACTIONS", "GET_TRANSACTIONS_XML"),
}


def _operation_flags(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for operation, supported in value.items():
        name = text(getattr(operation, "name", operation), 64).upper()
        if name and type(supported) is bool:
            result[name] = supported
    return result


def _operation_status(flags: dict[str, bool], feature: str) -> bool | None:
    names = _READ_OPERATION_NAMES[feature]
    values = [flags[name] for name in names if name in flags]
    if any(values):
        return True
    if len(values) == len(names):
        return False
    return None


def _combine_operation_status(bank: bool | None, account: bool | None) -> bool | None:
    if bank is False or account is False:
        return False
    if bank is True or account is True:
        return True
    return None


def _information_account_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    account = SimpleNamespace(
        iban=value.get("iban", ""),
        bank_identifier=value.get("bank_identifier", ""),
        accountnumber=value.get("account_number", ""),
        subaccount=value.get("subaccount_number", ""),
    )
    try:
        return _remote_id(account)
    except ValueError:
        return ""


def _public_capabilities(information: Any) -> dict[str, Any]:
    """Keep only bounded read capabilities and TAN procedure descriptions."""
    if not isinstance(information, dict):
        raise ValueError("FinTS lieferte ein unbekanntes Fähigkeitsformat")
    bank = information.get("bank")
    bank_flags = _operation_flags(bank.get("supported_operations") if isinstance(bank, dict) else None)
    read = {feature: _operation_status(bank_flags, feature) for feature in _READ_OPERATION_NAMES}
    accounts: dict[str, dict[str, bool | None]] = {}
    rows = information.get("accounts")
    if isinstance(rows, Iterable) and not isinstance(rows, (str, bytes, dict)):
        for index, row in enumerate(rows):
            if index >= _MAX_CAPABILITY_ACCOUNTS:
                break
            remote_id = _information_account_id(row)
            if not remote_id or remote_id in accounts:
                continue
            account_flags = _operation_flags(row.get("supported_operations"))
            accounts[remote_id] = {
                feature: _combine_operation_status(
                    read[feature], _operation_status(account_flags, feature),
                )
                for feature in _READ_OPERATION_NAMES
            }

    auth = information.get("auth")
    auth = auth if isinstance(auth, dict) else {}
    raw_methods = auth.get("tan_mechanisms")
    methods = []
    if isinstance(raw_methods, dict):
        for index, (security_function, mechanism) in enumerate(raw_methods.items()):
            if index >= _MAX_TAN_MECHANISMS:
                break
            identifier = text(security_function, 16)
            if not identifier:
                continue
            version = getattr(mechanism, "VERSION", None)
            methods.append({
                "security_function": identifier,
                "name": text(getattr(mechanism, "name", ""), 80),
                "version": version if type(version) is int and 1 <= version <= 99 else None,
                "tan_process": text(getattr(mechanism, "tan_process", ""), 8),
                "challenge_structured": (
                    getattr(mechanism, "challenge_structured")
                    if type(getattr(mechanism, "challenge_structured", None)) is bool else None
                ),
            })
    methods.sort(key=lambda item: (item["name"].casefold(), item["security_function"]))
    current_method = text(auth.get("current_tan_mechanism", ""), 16)
    method_names = {item["security_function"]: item["name"] for item in methods}
    if current_method == "999":
        current_method_label = "Ein-Schritt-Verfahren (PIN)"
    else:
        current_method_label = method_names.get(current_method, current_method)
    return {
        "read": read,
        "accounts": accounts,
        "tan_mechanisms": methods,
        "current_tan_mechanism": current_method,
        "current_tan_mechanism_label": current_method_label,
    }


def _capability_profile(client: Any) -> dict[str, Any]:
    get_information = getattr(client, "get_information", None)
    if not callable(get_information):
        return _public_capabilities({})
    try:
        information = get_information()
        return _public_capabilities(information)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("FinTS-Fähigkeitsdaten konnten nicht sicher ausgewertet werden") from exc


def _require_read_capability(client: Any, remote_account_id: str, feature: str) -> None:
    profile = _capability_profile(client)
    status = profile["accounts"].get(remote_account_id, {}).get(feature)
    if status is None:
        status = profile["read"].get(feature)
    if status is False:
        label = "Saldoabfrage" if feature == "balance" else "Umsatzabruf"
        raise FinTSFeatureUnavailable(f"Die Bank unterstützt den Lesezugriff auf {label} für dieses Konto nicht.")


def discover_accounts(connection: dict[str, Any], pin: str) -> list[dict[str, str]]:
    """Retry transient transport failures during a fresh, read-only account lookup."""
    return run_read_with_bounded_retry(
        lambda: _discover_accounts_once(connection, pin),
    )


def _discover_accounts_once(connection: dict[str, Any], pin: str) -> list[dict[str, str]]:
    client = _client(connection, pin)
    try:
        accounts = _sepa_accounts(client)
        return [normalize_account(account).as_dict() for account in accounts]
    finally:
        del client


def discover_accounts_with_capabilities(
    connection: dict[str, Any], pin: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Discover SEPA accounts and safely summarize the bank's read/TAN capabilities."""
    return run_read_with_bounded_retry(
        lambda: _discover_accounts_with_capabilities_once(connection, pin),
    )


def _discover_accounts_with_capabilities_once(
    connection: dict[str, Any], pin: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    client = _client(connection, pin)
    try:
        accounts = [normalize_account(account).as_dict() for account in _sepa_accounts(client)]
        return accounts, _capability_profile(client)
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
    accounts = _sepa_accounts(client)
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
    challenge_state: dict[str, Any] | None = None,
    tan: str = "",
) -> list[dict[str, Any]]:
    """Read one bounded booked-transaction window or resume its read-only SCA."""
    if end_date < start_date:
        raise ValueError("Abrufende liegt vor Abrufbeginn")
    if (end_date - start_date).days > 370:
        raise ValueError("Ein einzelner FinTS-Abruf ist auf 370 Tage begrenzt")
    if len(str(tan or "")) > 256 or "\x00" in str(tan or ""):
        raise ValueError("TAN-Eingabe ist ungültig")
    if challenge_state is None:
        client = _client(connection, pin)
        try:
            retry_response = None
            step = "transactions"
            account = None
            try:
                account = _find_remote_account(client, remote_account_id)
            except FinTSAuthenticationRequired as exc:
                retry_response = exc.challenge_response
                step = "accounts"
            if retry_response is None:
                _require_read_capability(client, remote_account_id, "transactions")
                result = client.get_transactions(account, start_date, end_date)
                if is_tan_response(result):
                    retry_response = result
            if retry_response is not None:
                raise _transaction_challenge_error(
                    client, retry_response, remote_account_id, start_date, end_date,
                    step=step, account=account,
                )
            return _normalize_transactions(result)
        finally:
            del client

    if (
        challenge_state.get("operation") != "transactions"
        or challenge_state.get("remote_account_id") != remote_account_id
        or challenge_state.get("start_date") != start_date.isoformat()
        or challenge_state.get("end_date") != end_date.isoformat()
        or challenge_state.get("step") not in {"accounts", "transactions"}
    ):
        raise ValueError("FinTS-SCA-Sitzung passt nicht zu diesem Abruf; bitte erneut starten")
    retry_response = _restore_sca_retry_response(
        challenge_state.get("challenge_data"), decoupled=challenge_state.get("decoupled"),
    )
    if not retry_response.decoupled and not str(tan or ""):
        raise ValueError("Die Bank verlangt eine TAN")
    client = _client(connection, pin, from_data=_decode_sca_state(challenge_state.get("client_data")))
    next_retry = None
    next_step = str(challenge_state["step"])
    next_account = None
    result = None
    if next_step == "transactions":
        _require_read_capability(client, remote_account_id, "transactions")
    with client.resume_dialog(_decode_sca_state(challenge_state.get("dialog_data"))):
        result = client.send_tan(retry_response, "" if retry_response.decoupled else str(tan))
        if is_tan_response(result):
            next_retry = result
        elif next_step == "accounts":
            if not isinstance(result, Iterable) or isinstance(result, (str, bytes, dict)):
                raise ValueError("FinTS lieferte ein unbekanntes Kontenformat")
            next_account = next(
                (item for item in result if _remote_id(item) == remote_account_id), None,
            )
            if next_account is None:
                raise ValueError("Das zugeordnete Bankkonto wurde beim FinTS-Abruf nicht gefunden")
            _require_read_capability(client, remote_account_id, "transactions")
            result = client.get_transactions(next_account, start_date, end_date)
            if is_tan_response(result):
                next_retry = result
                next_step = "transactions"
        if next_retry is not None:
            dialog_data = client.pause_dialog()
    if next_retry is not None:
        error = _transaction_challenge_error(
            client, next_retry, remote_account_id, start_date, end_date,
            step=next_step, account=next_account,
            completed_mappings=challenge_state.get("completed_mappings", []),
            dialog_data=dialog_data,
        )
        del client
        raise error
    del client
    return _normalize_transactions(result)


def _normalize_transactions(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if is_tan_response(result) or not isinstance(result, Iterable) or isinstance(result, (str, bytes, dict)):
        raise ValueError("FinTS lieferte ein unbekanntes Umsatzformat")
    return [normalize_transaction(item) for item in result]


def _transaction_challenge_error(
    client: Any,
    retry_response: Any,
    remote_account_id: str,
    start_date: date,
    end_date: date,
    *,
    step: str,
    account: Any = None,
    completed_mappings: list[dict[str, str]] | None = None,
    dialog_data: bytes | None = None,
) -> FinTSAuthenticationRequired:
    if not is_tan_response(retry_response):
        raise ValueError("FinTS lieferte eine unbekannte TAN-Herausforderung")
    state = {
        "version": 1,
        "operation": "transactions",
        "step": step,
        "client_data": _encode_sca_state(client.deconstruct()),
        "dialog_data": _encode_sca_state(dialog_data if dialog_data is not None else client.pause_dialog()),
        "challenge_data": _encode_sca_state(retry_response.get_data()),
        "remote_account_id": remote_account_id,
        "account_currency": text(getattr(account, "currency", "EUR"), 3).upper() or "EUR",
        "challenge_text": text(getattr(retry_response, "challenge", ""), 2000),
        "decoupled": getattr(retry_response, "decoupled", False) is True,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "local_account_id": "",
        "completed_mappings": list(completed_mappings or []),
    }
    return FinTSAuthenticationRequired(
        "Die Bank verlangt eine TAN/App-Freigabe für den Umsatzabruf.",
        challenge_state=state,
        challenge_text=state["challenge_text"],
        decoupled=state["decoupled"],
    )


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
