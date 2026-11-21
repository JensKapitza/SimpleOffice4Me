"""Admin-triggered per-peer capability checks (file, tone, QR, print dry-run)."""
from __future__ import annotations

import hashlib
import http.client
import json
import secrets
import time
from pathlib import Path
from typing import Any

from .federation_discovery_endpoint import (
    _connection_for,
    _host_header,
    _validated_target,
    normalize_endpoint,
)
from .federation_store import FederationStore

TEST_TIMEOUT_SECONDS = 15
TEST_FILE_BYTES = 1024


def _peer(root: str | Path, peer_id: str) -> dict[str, Any]:
    store = FederationStore(root)
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Federation-Peer ist nicht aktiv")
    return peer


def _post(peer: dict[str, Any], token: str, path: str, body: bytes = b"",
          content_type: str = "application/json") -> tuple[int, dict[str, Any]]:
    base_url = normalize_endpoint(peer.get("base_url", ""))
    _canonical, parsed, port, addresses = _validated_target(base_url)
    if not addresses:
        raise ValueError("peer address did not resolve")
    headers = {
        "Accept": "application/json",
        "Content-Type": content_type,
        "Authorization": f"Bearer {token}",
        "Host": _host_header(parsed.hostname or "", port, parsed.scheme),
        "User-Agent": "SimpleOffice4Me-Federation-PeerTests/1",
    }
    target = str(parsed.path or "").rstrip("/") + path
    connection = _connection_for(parsed.scheme, addresses[0].compressed, port,
                                 parsed.hostname or "", TEST_TIMEOUT_SECONDS)
    try:
        connection.request("POST", target, body=body or None, headers=headers)
        response = connection.getresponse()
        raw = response.read(65536 + 1)
        status = int(response.status or 200)
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        raise ValueError(f"peer unreachable: {type(exc).__name__}") from exc
    finally:
        try:
            connection.close()
        except OSError:
            pass
    if status >= 400:
        raise ValueError(f"peer answered {status}")
    if len(raw) > 65536:
        raise ValueError("peer answer is too large")
    try:
        value = json.loads((raw or b"{}").decode("utf-8"))
    except ValueError as exc:
        raise ValueError("peer answer is not JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("peer answer is not a JSON object")
    return status, value


def check_file_send(root: str | Path, peer_id: str) -> dict[str, Any]:
    """Send 1 KiB to the peer echo endpoint and compare the hash."""
    store = FederationStore(root)
    peer = _peer(root, peer_id)
    payload = secrets.token_bytes(TEST_FILE_BYTES)
    expected = hashlib.sha256(payload).hexdigest()
    started = time.monotonic()
    status, answer = _post(peer, store.peer_token(peer["peer_id"]),
                           "/federation/v1/blocks/test", payload,
                           "application/octet-stream")
    ok = status == 200 and answer.get("ok") is True and answer.get("sha256") == expected
    result = {"ok": ok, "sha256": answer.get("sha256", ""),
              "latency_ms": int((time.monotonic() - started) * 1000)}
    store.record_event("peer_test_file_send", peer_id=peer["peer_id"], detail=result)
    if not ok:
        raise ValueError("file echo mismatch")
    return result


def check_tone(root: str | Path, peer_id: str) -> dict[str, Any]:
    """Trigger a short test tone on the peer and report both stages."""
    store = FederationStore(root)
    peer = _peer(root, peer_id)
    started = time.monotonic()
    status, answer = _post(peer, store.peer_token(peer["peer_id"]),
                           "/federation/v1/audio/test-tone", b"{}")
    result = {"ok": status == 200 and answer.get("ok") is True,
              "rendered": bool(answer.get("rendered")),
              "played": bool(answer.get("played")),
              "player_error": str(answer.get("player_error", "") or "")[:160],
              "latency_ms": int((time.monotonic() - started) * 1000)}
    store.record_event("peer_test_tone", peer_id=peer["peer_id"], detail=result)
    if not result["ok"]:
        raise ValueError("tone test rejected")
    return result


def check_print(root: str | Path, peer_id: str) -> dict[str, Any]:
    """Run a federated print dry-run (call verified, nothing spooled)."""
    from .printershare import remote_print_capabilities, submit_remote_print

    store = FederationStore(root)
    peer = _peer(root, peer_id)
    capabilities = remote_print_capabilities(root, peer["peer_id"])
    if not capabilities.get("enabled"):
        raise ValueError("peer offers no federated printing")
    printers = [item for item in capabilities.get("printers", []) if isinstance(item, dict)]
    if not printers:
        raise ValueError("peer shares no printer")
    started = time.monotonic()
    result = submit_remote_print(
        root, peer["peer_id"], str(printers[0].get("printer_id")),
        b"Federation print dry-run (no spooling)",
        content_type="text/plain", filename="Testdruck Federation",
        retention_ceiling="no_store", dry_run=True,
    )
    outcome = {"ok": True, "printer_id": str(printers[0].get("printer_id")),
               "job_id": str(result.get("receipt", {}).get("job_id", "")),
               "latency_ms": int((time.monotonic() - started) * 1000)}
    store.record_event("peer_test_print", peer_id=peer["peer_id"], detail=outcome)
    return outcome


def check_qr(root: str | Path) -> dict[str, Any]:
    """Round-trip the local profile through QR encode/decode locally."""
    from .federation_local_profile import local_peer_id, local_profile
    from .federation_qr import decode_peer, encode_peer

    profile = local_profile(root if root is not None else None, "")
    payload = encode_peer(profile)
    decoded = decode_peer(payload)
    ok = (decoded["peer_id"] == local_peer_id()
          and decoded["fingerprint"] == profile["fingerprint"]
          and decoded["base_url"] == profile["base_url"])
    if not ok:
        raise ValueError("QR round-trip mismatch")
    return {"ok": True, "peer_id": decoded["peer_id"],
            "fingerprint": decoded["fingerprint"][:16]}
