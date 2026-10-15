"""Exercise public discovery, peer HMAC and V3 receive contracts across the test net."""
from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


os.environ.setdefault("SIMPLEOFFICE_LOG_STDERR_ONLY", "1")

from app.federation_peer_auth import headers as peer_headers


TIMEOUT_SECONDS = 5
GLOBAL_TOKEN = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "")
LOCAL_PEER_ID = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "")
LOCAL_PEER_TOKEN = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_LOCAL_PEER_TOKEN", "")
REMOTE_PEER_ID = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_ID", "")
REMOTE_PEER_URL = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_URL", "").rstrip("/")


def _request(
    url: str,
    *,
    body: bytes | None = None,
    bearer: str = "",
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    target = urlsplit(url)
    if (
        target.scheme != "http"
        or target.hostname not in {"peer-a", "peer-b"}
        or target.port not in (None, 8080)
        or target.username is not None
        or target.password is not None
        or target.fragment
    ):
        raise ValueError("federation smoke requests may only target peer-a or peer-b on port 8080")
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        # The test harness is intentionally confined to its isolated Compose peers.
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-receive",
        choices=("direct_trust_required", "pending", "duplicate"),
        default="direct_trust_required",
    )
    expected_receive = parser.parse_args().expect_receive
    _expect(
        all((GLOBAL_TOKEN, LOCAL_PEER_ID, LOCAL_PEER_TOKEN, REMOTE_PEER_ID, REMOTE_PEER_URL)),
        "test peer configuration is incomplete",
    )
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
            f"{base_url}/federation/v3/capabilities", bearer=GLOBAL_TOKEN,
        )
        _expect(status == 200, f"{hostname} V3 capabilities returned HTTP {status}")
        _expect(
            capabilities.get("contract") == "simpleoffice-federation-envelope"
            and capabilities.get("envelope_versions") == [1]
            and capabilities.get("objects", {}).get("documents") == [1],
            f"{hostname} advertised an unexpected V3 contract",
        )

    local_url = os.environ.get("SIMPLEOFFICE_FEDERATION_PUBLIC_URL", "").rstrip("/")
    local_status, local_capabilities = _request(
        f"{local_url}/federation/v3/capabilities", bearer=GLOBAL_TOKEN,
    )
    _expect(local_status == 200, "local V3 capabilities were unavailable")
    capability_path = f"/federation/v3/peers/{LOCAL_PEER_ID}/capabilities"
    capability_body = json.dumps(local_capabilities, separators=(",", ":")).encode("utf-8")
    status, negotiated = _request(
        REMOTE_PEER_URL + capability_path,
        body=capability_body,
        extra_headers=peer_headers(
            LOCAL_PEER_ID, LOCAL_PEER_TOKEN, "POST", capability_path, capability_body,
        ),
    )
    _expect(
        status == 200 and negotiated.get("compatible") is True,
        "the remote peer rejected the authenticated capability exchange",
    )

    anonymous_path = "/federation/v3/receive"
    anonymous_body = json.dumps({"message_id": "compose-global-token-check-0001"}, separators=(",", ":")).encode("utf-8")
    status, denied = _request(
        REMOTE_PEER_URL + anonymous_path,
        body=anonymous_body,
        bearer=GLOBAL_TOKEN,
    )
    _expect(
        status == 401 and denied.get("error") == "peer_authentication_required",
        "a server bearer unexpectedly established a peer identity",
    )

    message_id = {
        "direct_trust_required": "compose-smoke-untrusted-0001",
        "pending": "compose-smoke-pending-0001",
        "duplicate": "compose-smoke-pending-0001",
    }[expected_receive]
    envelope = {
        "message_id": message_id,
        "sender_instance": LOCAL_PEER_ID,
        "recipient_instance": REMOTE_PEER_ID,
        "type": "documents",
        "schema_version": 1,
        "envelope_version": 1,
        "time": "2026-10-13T00:00:00Z",
        "payload": {"operation": "offer"},
    }
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    status, received = _request(
        REMOTE_PEER_URL + anonymous_path,
        body=body,
        extra_headers=peer_headers(
            LOCAL_PEER_ID, LOCAL_PEER_TOKEN, "POST", anonymous_path, body,
        ),
    )
    expected_status, expected_result, expected_error = {
        "direct_trust_required": (409, "rejected", "direct_trust_required"),
        "pending": (202, "pending", ""),
        "duplicate": (200, "duplicate", ""),
    }[expected_receive]
    actual_result = str(received.get("status", ""))
    actual_error = str(received.get("error", ""))
    _expect(
        status == expected_status
        and actual_result == expected_result
        and actual_error == expected_error,
        (
            f"authenticated receive returned HTTP {status}, "
            f"status={actual_result!r}, error={actual_error!r}; "
            f"expected {expected_receive}"
        ),
    )
    print("PASS: independent discovery identities and V3 capabilities")
    print("PASS: the compatibility bearer cannot impersonate a peer")
    print(f"PASS: peer-HMAC receive result is {expected_receive}")


if __name__ == "__main__":
    main()
