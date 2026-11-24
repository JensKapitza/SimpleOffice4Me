"""Pinned outbound client for the federation pairing handshake."""
from __future__ import annotations

import hmac
import http.client
import json
import ssl
from typing import Any

from .federation_discovery_endpoint import (
    _connection_for,
    _host_header,
    _validated_target,
)
from .federation_discovery_service import _is_explicit_local_endpoint
from .federation_pairing_protocol import build_pairing_request
from .federation_peer_profile import peer_profile
from .translations import translate


_PAIRING_PATHS = {
    "claim": "/federation/v1/pairing/claim",
    "status": "/federation/v1/pairing/status",
}
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_DISCOVERY_BYTES = 1024 * 1024
_USER_AGENT = "SimpleOffice4Me-Federation-Pairing/1"


class PairingClientError(ValueError):
    """A user-safe pairing failure that never includes network internals or the code."""

    def __init__(self, message: str, *, reason: str = ""):
        super().__init__(message)
        self.reason = reason

    def user_message(self, language: str) -> str:
        if self.reason:
            return translate(language, f"fedpeer.pairing.error.{self.reason}")
        return str(self)


def _read_json(response, limit: int) -> dict[str, Any]:
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise PairingClientError("Antwort der Gegenstelle ist unerwartet groß.", reason="response")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PairingClientError("Gegenstelle lieferte keine gültige Pairing-Antwort.", reason="response") from exc
    if not isinstance(value, dict):
        raise PairingClientError("Gegenstelle lieferte keine gültige Pairing-Antwort.", reason="response")
    return value


def _request(connection, method: str, path: str, host: str, port: int, scheme: str, payload=None):
    body = None
    headers = {
        "Accept": "application/json",
        "Host": _host_header(host, port, scheme),
        "User-Agent": _USER_AGENT,
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    return connection.getresponse()


def _verify_remote_identity(connection, parsed, port: int, expected: dict[str, Any]) -> None:
    base_path = str(parsed.path or "").rstrip("/")
    response = _request(
        connection,
        "GET",
        base_path + "/.well-known/simpleoffice-federation",
        parsed.hostname,
        port,
        parsed.scheme,
    )
    if response.status != 200:
        raise PairingClientError("Identität der Gegenstelle konnte nicht geprüft werden.", reason="profile")
    try:
        profile = peer_profile(_read_json(response, _MAX_DISCOVERY_BYTES))
    except ValueError as exc:
        raise PairingClientError("Identität der Gegenstelle ist ungültig.", reason="profile") from exc
    expected_peer_id = str(expected.get("peer_id") or "")
    expected_key = str(expected.get("public_key") or "")
    expected_fingerprint = str(expected.get("fingerprint") or "")
    if (
        profile["peer_id"] != expected_peer_id
        or not expected_key
        or not expected_fingerprint
        or not hmac.compare_digest(profile["public_key"], expected_key)
        or not hmac.compare_digest(profile["fingerprint"], expected_fingerprint)
    ):
        raise PairingClientError(
            "Identität der Gegenstelle stimmt nicht mit dem bekannten Peer überein.",
            reason="identity_mismatch",
        )


def _pairing_request(root: str, peer: dict[str, Any], operation: str, *, code: str = "") -> dict[str, Any]:
    if operation not in _PAIRING_PATHS:
        raise ValueError("invalid pairing operation")
    base_url = str(peer.get("base_url") or "")
    stage = "endpoint"
    try:
        allow_private = True if _is_explicit_local_endpoint(base_url) else None
        _canonical, parsed, port, addresses = _validated_target(
            base_url,
            allow_private=allow_private,
        )
        stage = "transport"
        connection = _connection_for(
            parsed.scheme,
            addresses[0].compressed,
            port,
            parsed.hostname,
            10,
        )
        try:
            _verify_remote_identity(connection, parsed, port, peer)
        finally:
            connection.close()

        stage = "local_identity"
        payload = build_pairing_request(root, operation, code=code)
        stage = "transport"
        connection = _connection_for(
            parsed.scheme,
            addresses[0].compressed,
            port,
            parsed.hostname,
            10,
        )
        try:
            base_path = str(parsed.path or "").rstrip("/")
            response = _request(
                connection,
                "POST",
                base_path + _PAIRING_PATHS[operation],
                parsed.hostname,
                port,
                parsed.scheme,
                payload,
            )
            if response.status in {404, 405}:
                raise PairingClientError("Pairing-Endpunkt ist nicht verfügbar.", reason="remote_endpoint")
            value = _read_json(response, _MAX_RESPONSE_BYTES)
        finally:
            connection.close()
    except PairingClientError:
        raise
    except ssl.SSLError as exc:
        raise PairingClientError("TLS zur Gegenstelle konnte nicht geprüft werden.", reason="tls") from exc
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise PairingClientError(
            "Pairing-Anfrage konnte nicht ausgeführt werden.", reason=stage,
        ) from exc

    if response.status == 400 and value.get("error") == "invalid_or_expired_pairing_code":
        raise PairingClientError("Pairing-Code ist ungültig, abgelaufen oder bereits verwendet.", reason="code")
    if response.status == 401 and value.get("error") == "pairing_authentication_failed":
        raise PairingClientError(
            "Gegenstelle hat die bekannte lokale Peer-Identität nicht akzeptiert.",
            reason="identity_rejected",
        )
    pairing = value.get("pairing")
    if response.status != 200 or not isinstance(pairing, dict):
        raise PairingClientError("Gegenstelle hat die Pairing-Anfrage abgewiesen.", reason="response")
    status = str(pairing.get("status") or "")
    if status not in {"missing", "pending", "claimed", "expired"}:
        raise PairingClientError("Gegenstelle lieferte einen unbekannten Pairing-Status.", reason="response")
    response_peer_id = str(pairing.get("peer_id") or "")
    if response_peer_id != payload["peer_id"]:
        raise PairingClientError("Gegenstelle lieferte einen Pairing-Status für den falschen Peer.", reason="response")
    if operation == "claim" and status != "claimed":
        raise PairingClientError("Gegenstelle hat den Pairing-Code nicht beansprucht.", reason="response")
    return {"peer_id": response_peer_id, "status": status}


def claim_pairing_code(root: str, peer: dict[str, Any], code: str) -> dict[str, Any]:
    """Claim a remote code after pinning and rechecking the known peer identity."""
    value = str(code or "")
    if not value or len(value) > 64:
        raise PairingClientError("Pairing-Code ist ungültig, abgelaufen oder bereits verwendet.", reason="code")
    return _pairing_request(root, peer, "claim", code=value)


def remote_pairing_status(root: str, peer: dict[str, Any]) -> dict[str, Any]:
    """Read this instance's non-secret pairing status from a known peer."""
    return _pairing_request(root, peer, "status")
