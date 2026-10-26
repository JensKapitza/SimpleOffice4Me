"""Pinned outbound client for the federation pairing handshake."""
from __future__ import annotations

import hmac
import json
from typing import Any

from .federation_discovery_endpoint import (
    connection_for,
    host_header,
    _validated_target,
)
from .federation_discovery_service import _is_explicit_local_endpoint
from .federation_pairing_protocol import build_pairing_request
from .federation_peer_profile import peer_profile


_PAIRING_PATHS = {
    "claim": "/federation/v1/pairing/claim",
    "status": "/federation/v1/pairing/status",
}
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_DISCOVERY_BYTES = 1024 * 1024
_USER_AGENT = "SimpleOffice4Me-Federation-Pairing/1"


class PairingClientError(ValueError):
    """A user-safe pairing failure that never includes the submitted code."""


def _read_json(response, limit: int) -> dict[str, Any]:
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise PairingClientError("Antwort der Gegenstelle ist unerwartet groß.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PairingClientError("Gegenstelle lieferte keine gültige Pairing-Antwort.") from exc
    if not isinstance(value, dict):
        raise PairingClientError("Gegenstelle lieferte keine gültige Pairing-Antwort.")
    return value


def _request(connection, method: str, path: str, host: str, port: int, scheme: str, payload=None):
    body = None
    headers = {
        "Accept": "application/json",
        "Host": host_header(host, port, scheme),
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
        raise PairingClientError("Identität der Gegenstelle konnte nicht geprüft werden.")
    try:
        profile = peer_profile(_read_json(response, _MAX_DISCOVERY_BYTES))
    except ValueError as exc:
        raise PairingClientError("Identität der Gegenstelle ist ungültig.") from exc
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
            "Identität der Gegenstelle stimmt nicht mit dem bekannten Peer überein."
        )


def _pairing_request(root: str, peer: dict[str, Any], operation: str, *, code: str = "") -> dict[str, Any]:
    if operation not in _PAIRING_PATHS:
        raise ValueError("invalid pairing operation")
    base_url = str(peer.get("base_url") or "")
    allow_private = True if _is_explicit_local_endpoint(base_url) else None
    try:
        _canonical, parsed, port, addresses = _validated_target(
            base_url,
            allow_private=allow_private,
        )
        connection = connection_for(
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

        payload = build_pairing_request(root, operation, code=code)
        connection = connection_for(
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
            value = _read_json(response, _MAX_RESPONSE_BYTES)
        finally:
            connection.close()
    except PairingClientError:
        raise
    except (OSError, ValueError) as exc:
        raise PairingClientError(
            "Gegenstelle ist nicht erreichbar oder ihre Netzwerkadresse ist nicht zulässig."
        ) from exc

    if response.status == 400 and value.get("error") == "invalid_or_expired_pairing_code":
        raise PairingClientError("Pairing-Code ist ungültig, abgelaufen oder bereits verwendet.")
    if response.status == 401 and value.get("error") == "pairing_authentication_failed":
        raise PairingClientError(
            "Gegenstelle hat die bekannte lokale Peer-Identität nicht akzeptiert."
        )
    pairing = value.get("pairing")
    if response.status != 200 or not isinstance(pairing, dict):
        raise PairingClientError("Gegenstelle hat die Pairing-Anfrage abgewiesen.")
    status = str(pairing.get("status") or "")
    if status not in {"missing", "pending", "claimed", "expired"}:
        raise PairingClientError("Gegenstelle lieferte einen unbekannten Pairing-Status.")
    response_peer_id = str(pairing.get("peer_id") or "")
    if response_peer_id != payload["peer_id"]:
        raise PairingClientError("Gegenstelle lieferte einen Pairing-Status für den falschen Peer.")
    if operation == "claim" and status != "claimed":
        raise PairingClientError("Gegenstelle hat den Pairing-Code nicht beansprucht.")
    return {"peer_id": response_peer_id, "status": status}


def claim_pairing_code(root: str, peer: dict[str, Any], code: str) -> dict[str, Any]:
    """Claim a remote code after pinning and rechecking the known peer identity."""
    value = str(code or "")
    if not value or len(value) > 64:
        raise PairingClientError("Pairing-Code ist ungültig, abgelaufen oder bereits verwendet.")
    return _pairing_request(root, peer, "claim", code=value)


def remote_pairing_status(root: str, peer: dict[str, Any]) -> dict[str, Any]:
    """Read this instance's non-secret pairing status from a known peer."""
    return _pairing_request(root, peer, "status")
