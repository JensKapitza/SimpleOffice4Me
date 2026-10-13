"""Exercise public discovery and V3 auth contracts across the isolated test net."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


TIMEOUT_SECONDS = 5
TOKEN = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "")


def _request(url: str, *, body: bytes | None = None, bearer: str = "") -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            return error.code, json.load(error)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return error.code, {}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    _expect(bool(TOKEN), "test bearer is not configured")
    expected_peers = {
        "peer-a": ("federation-test-peer-a", "http://peer-a:8080"),
        "peer-b": ("federation-test-peer-b", "http://peer-b:8080"),
    }
    for hostname, (peer_id, base_url) in expected_peers.items():
        status, profile = _request(f"{base_url}/.well-known/simpleoffice-federation")
        _expect(status == 200, f"{hostname} discovery returned HTTP {status}")
        _expect(
            (profile.get("peer_id"), profile.get("base_url")) == (peer_id, base_url),
            f"{hostname} advertised an unexpected federation identity",
        )
        status, capabilities = _request(
            f"{base_url}/federation/v3/capabilities", bearer=TOKEN,
        )
        _expect(status == 200, f"{hostname} V3 capabilities returned HTTP {status}")
        _expect(
            capabilities.get("contract") == "simpleoffice-federation-envelope"
            and capabilities.get("envelope_versions") == [1]
            and capabilities.get("objects", {}).get("documents") == [1],
            f"{hostname} advertised an unexpected V3 contract",
        )

    envelope = {
        "message_id": "compose-smoke-message-0001",
        "sender_instance": "federation-test-peer-a",
        "recipient_instance": "federation-test-peer-b",
        "type": "documents",
        "schema_version": 1,
        "envelope_version": 1,
        "time": "2026-10-13T00:00:00Z",
        "payload": {"operation": "offer"},
    }
    status, denied = _request(
        "http://peer-b:8080/federation/v3/receive",
        body=json.dumps(envelope, separators=(",", ":")).encode("utf-8"),
        bearer=TOKEN,
    )
    _expect(
        status == 401 and denied.get("error") == "peer_authentication_required",
        "a server bearer unexpectedly established a peer identity",
    )
    print("PASS: independent discovery identities and V3 capabilities")
    print("PASS: the compatibility bearer cannot impersonate a peer")


if __name__ == "__main__":
    main()
