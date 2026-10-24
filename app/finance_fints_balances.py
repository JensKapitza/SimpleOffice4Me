"""Read-only FinTS balance adapter with short-lived TAN challenge support."""
from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from .finance_schema import currency as _currency, text
from .finance_fints import (
    FinTSAuthenticationRequired,
    _require_read_capability,
    _client,
    _decode_sca_state as _decode_state,
    _encode_sca_state as _encode_state,
    _find_remote_account,
    _restore_sca_retry_response as _restore_retry_response,
    is_tan_response,
)

def _client_context(client: Any):
    return client if callable(getattr(client, "__enter__", None)) else nullcontext(client)


def _decimal_amount(value: Any) -> str:
    """Normalize python-fints balance amounts without floating-point rounding."""
    if hasattr(value, "amount"):
        value = value.amount
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("FinTS lieferte einen ungültigen Kontostand") from exc
    if not amount.is_finite():
        raise ValueError("FinTS lieferte einen ungültigen Kontostand")
    try:
        return str(amount.quantize(Decimal("0.01")))
    except InvalidOperation as exc:
        raise ValueError("FinTS lieferte einen ungültigen Kontostand") from exc


def _challenge_error(
    client: Any,
    retry_response: Any,
    dialog_data: bytes,
    *,
    remote_account_id: str,
    account_currency: str,
    step: str,
) -> FinTSAuthenticationRequired:
    if not is_tan_response(retry_response):
        raise ValueError("FinTS lieferte eine unbekannte TAN-Herausforderung")
    retry_data = retry_response.get_data()
    client_data = client.deconstruct()
    challenge_state = {
        "version": 1,
        "operation": "balance",
        "step": step,
        "client_data": _encode_state(client_data),
        "dialog_data": _encode_state(dialog_data),
        "challenge_data": _encode_state(retry_data),
        "remote_account_id": remote_account_id,
        "account_currency": _currency(account_currency or "EUR"),
        "challenge_text": text(getattr(retry_response, "challenge", ""), 2000),
        "decoupled": getattr(retry_response, "decoupled", False) is True,
    }
    return FinTSAuthenticationRequired(
        "Die Bank verlangt eine TAN/App-Freigabe für die Saldoabfrage.",
        challenge_state=challenge_state,
        challenge_text=challenge_state["challenge_text"],
        decoupled=challenge_state["decoupled"],
    )


def _normalize_balance(result: Any, remote_account_id: str, account: Any) -> dict[str, str]:
    if result is None or is_tan_response(result):
        raise ValueError("FinTS lieferte keinen gebuchten Kontostand")
    booked = getattr(result, "amount", None)
    if booked is None and isinstance(result, (Decimal, int, str)):
        booked = result
    if booked is None:
        raise ValueError("FinTS lieferte keinen gebuchten Kontostand")
    currency = getattr(result, "currency", None) or getattr(booked, "currency", None)
    currency = currency or getattr(account, "currency", None)
    available = getattr(result, "available_balance", None)
    if available is None:
        available = getattr(result, "available_amount", None)
    if callable(available):
        available = None
    normalized_currency = _currency(currency or "EUR")
    balance = {
        "remote_account_id": remote_account_id,
        "booked_balance": _decimal_amount(booked),
        "currency": normalized_currency,
    }
    balance["available_balance"] = _decimal_amount(available) if available is not None else ""
    balance["available_currency"] = (
        _currency(getattr(available, "currency", None) or normalized_currency)
        if available is not None else ""
    )
    return balance


def fetch_balance(
    connection: dict[str, Any],
    remote_account_id: str,
    pin: str,
    *,
    challenge_state: dict[str, Any] | None = None,
    tan: str = "",
) -> dict[str, str]:
    """Read a balance or continue one owner-bound, encrypted SCA operation."""
    if challenge_state is None:
        client = _client(connection, pin)
        retry_response = None
        dialog_data = None
        retry_step = ""
        account = None
        result = None
        with _client_context(client):
            try:
                account = _find_remote_account(client, remote_account_id)
            except FinTSAuthenticationRequired as exc:
                retry_response = exc.challenge_response
                retry_step = "accounts"
            if retry_response is None:
                _require_read_capability(client, remote_account_id, "balance")
                result = client.get_balance(account)
                if is_tan_response(result):
                    retry_response = result
                    retry_step = "balance"
            if retry_response is not None:
                dialog_data = client.pause_dialog()
        if retry_response is not None:
            raise _challenge_error(
                client, retry_response, dialog_data,
                remote_account_id=remote_account_id,
                account_currency=getattr(account, "currency", "EUR") if account is not None else "EUR",
                step=retry_step,
            )
        return _normalize_balance(result, remote_account_id, account)

    if challenge_state.get("operation") != "balance" or challenge_state.get("remote_account_id") != remote_account_id:
        raise ValueError("FinTS-SCA-Sitzung passt nicht zu diesem Konto; bitte erneut starten")
    if len(str(tan or "")) > 256 or "\x00" in str(tan or ""):
        raise ValueError("TAN-Eingabe ist ungültig")
    client_data = _decode_state(challenge_state.get("client_data"))
    dialog_blob = _decode_state(challenge_state.get("dialog_data"))
    retry_response = _restore_retry_response(
        challenge_state.get("challenge_data"),
        decoupled=challenge_state.get("decoupled"),
    )
    if challenge_state.get("step") not in {"accounts", "balance"}:
        raise ValueError("FinTS-SCA-Sitzung enthält einen ungültigen Verarbeitungsschritt")
    client = _client(connection, pin, from_data=client_data)
    if not retry_response.decoupled and not str(tan or ""):
        raise ValueError("Die Bank verlangt eine TAN")
    next_retry = None
    next_dialog = None
    result = None
    account = None
    if challenge_state.get("step") == "balance":
        _require_read_capability(client, remote_account_id, "balance")
    with client.resume_dialog(dialog_blob):
        result = client.send_tan(retry_response, "" if retry_response.decoupled else str(tan))
        if is_tan_response(result):
            next_retry = result
            next_dialog = client.pause_dialog()
        elif challenge_state.get("step") == "accounts":
            account = next((item for item in result if _account_id(item) == remote_account_id), None)
            if account is None:
                raise ValueError("Das zugeordnete Bankkonto wurde beim FinTS-Abruf nicht gefunden")
            _require_read_capability(client, remote_account_id, "balance")
            result = client.get_balance(account)
            if is_tan_response(result):
                next_retry = result
                next_dialog = client.pause_dialog()
    if next_retry is not None:
        raise _challenge_error(
            client, next_retry, next_dialog,
            remote_account_id=remote_account_id,
            account_currency=getattr(account, "currency", challenge_state.get("account_currency", "EUR")),
            step="balance" if account is not None else challenge_state.get("step", "balance"),
        )
    if account is None:
        account = SimpleNamespace(currency=challenge_state.get("account_currency", "EUR"))
    return _normalize_balance(result, remote_account_id, account)


def _account_id(account: Any) -> str:
    from .finance_fints import _remote_id
    return _remote_id(account)
