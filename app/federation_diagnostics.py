"""Authenticated, IP-pinned federation network diagnostics."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import time
from pathlib import Path

from flask import current_app

from .federation_discovery_endpoint import host_header, resolve_pinned_endpoint
from .federation_core import sanitize_peer_id
from .federation_local_profile import local_peer_id
from .federation_peer_auth import headers as peer_auth_headers
from .federation_store import FederationStore

PROBE_PATH = "/federation/v1/diagnostics/probe"
RESPONSE_VERSION = b"SOFP-DIAGNOSTIC-RESPONSE-V1\n"
PROBE_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 4096
RATE_LIMIT_COUNT = 10
RATE_LIMIT_WINDOW_SECONDS = 60
USER_AGENT = "SimpleOffice4Me-Federation-Diagnostics/1"
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def response_signature(token: str, nonce: str, body: bytes) -> str:
    canonical = RESPONSE_VERSION + str(nonce).encode("ascii") + b"\n" + body
    return hmac.new(str(token).encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def _record_failure(
    store: FederationStore,
    peer_id: str,
    started: float,
    category: str,
    stages: dict[str, str],
) -> dict:
    elapsed = int((time.monotonic() - started) * 1000)
    record = store.record_network_diagnostic(
        peer_id,
        direction="outbound",
        source="local",
        status="failure",
        category=category,
        latency_ms=elapsed,
        detail={"stages": stages},
    )
    return record


def _record_inbound(
    store: FederationStore, peer_id: str, *, status: str, category: str,
    detail: dict, checked_at: int | None = None,
) -> dict:
    record = store.record_network_diagnostic(
        peer_id,
        direction="inbound",
        source=peer_id,
        status=status,
        category=category,
        detail=detail,
        checked_at=checked_at,
    )
    return record


def _unavailable_peer(peer_id: str) -> dict:
    return {
        "peer_id": str(peer_id or "")[:128], "direction": "outbound", "source": "local",
        "checked_at": int(time.time()), "last_success_at": None, "status": "failure",
        "category": "configuration", "latency_ms": 0,
        "detail": {"stages": {"peer": "unavailable"}},
    }


def probe_peer(root: str | Path, peer_id: str, *, now: int | None = None) -> dict:
    """Run a signed outbound probe against one stored peer, never an arbitrary URL."""
    store = FederationStore(root)
    started = time.monotonic()
    stages = {"dns": "unknown", "tcp": "unknown", "tls": "not_applicable", "http": "unknown", "authentication": "unknown"}
    try:
        peer = store.get_peer(peer_id)
        if not peer:
            return _unavailable_peer(peer_id)
        peer_id = str(peer["peer_id"])
        if not peer.get("enabled"):
            return _record_failure(store, peer_id, started, "configuration", {**stages, "peer": "disabled"})
        token = store.peer_token(peer_id)
        if not token:
            return _record_failure(store, peer_id, started, "configuration", {**stages, "authentication": "missing_peer_token"})
        shared_bearer = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
        if not shared_bearer and not current_app.testing:
            return _record_failure(store, peer_id, started, "configuration", {**stages, "authentication": "shared_bearer_not_configured"})
        if not store.claim_diagnostic_attempt(peer_id, limit=RATE_LIMIT_COUNT, window_seconds=RATE_LIMIT_WINDOW_SECONDS):
            return _record_failure(store, peer_id, started, "rate_limited", stages)
    except (KeyError, RuntimeError, ValueError):
        return _unavailable_peer(peer_id)

    try:
        base_url, parsed, port, addresses = resolve_pinned_endpoint(peer["base_url"])
    except TimeoutError:
        stages["dns"] = "timeout"
        return _record_failure(store, peer_id, started, "dns_timeout", stages)
    except ValueError as exc:
        message = str(exc).casefold()
        category = "dns" if "resolve" in message else "policy" if any(word in message for word in ("private", "loopback", "network address", "endpoint")) else "configuration"
        stages["dns"] = "failed" if category == "dns" else "not_checked"
        return _record_failure(store, peer_id, started, category, stages)
    stages["dns"] = "ok"
    address = addresses[0]
    connect_host = address.compressed
    request_path = (parsed.path.rstrip("/") + PROBE_PATH) or PROBE_PATH
    probe_id = secrets.token_urlsafe(18)
    payload = _json_bytes({"probe_id": probe_id, "requested_at": int(time.time() if now is None else now)})
    try:
        proof = peer_auth_headers(local_peer_id(), token, "POST", request_path, payload)
    except (RuntimeError, ValueError):
        stages["authentication"] = "local_peer_identity_unavailable"
        return _record_failure(store, peer_id, started, "configuration", stages)
    bearer = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip() or token
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Host": host_header(parsed.hostname, port, parsed.scheme),
        **proof,
    }
    nonce = proof["X-SimpleOffice-Peer-Nonce"]
    connection = http.client.HTTPConnection(connect_host, port=port, timeout=PROBE_TIMEOUT_SECONDS)
    raw_socket = None
    try:
        raw_socket = socket.create_connection((connect_host, port), timeout=PROBE_TIMEOUT_SECONDS)
        stages["tcp"] = "ok"
        if parsed.scheme == "https":
            try:
                context = ssl.create_default_context()
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                connection.sock = context.wrap_socket(raw_socket, server_hostname=parsed.hostname)
                raw_socket = None
                stages["tls"] = "ok"
            except ssl.SSLCertVerificationError:
                stages["tls"] = "certificate_failed"
                return _record_failure(store, peer_id, started, "tls_certificate", stages)
            except ssl.SSLError:
                stages["tls"] = "failed"
                return _record_failure(store, peer_id, started, "tls", stages)
        else:
            connection.sock = raw_socket
            raw_socket = None
        connection.request("POST", request_path, body=payload, headers=headers)
        response = connection.getresponse()
        stages["http"] = str(response.status)
        if response.status == 401:
            stages["authentication"] = "rejected"
            return _record_failure(store, peer_id, started, "authentication", stages)
        if response.status == 403:
            stages["authentication"] = "authenticated_but_forbidden"
            return _record_failure(store, peer_id, started, "permission", stages)
        if response.status == 429:
            return _record_failure(store, peer_id, started, "rate_limited", stages)
        if response.status != 200:
            return _record_failure(store, peer_id, started, "http", stages)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return _record_failure(store, peer_id, started, "response_too_large", stages)
        stages["authentication"] = "checking_signature"
        supplied_signature = response.getheader("X-SimpleOffice-Diagnostic-Signature", "").casefold()
        expected_signature = response_signature(token, nonce, raw)
        if not hmac.compare_digest(expected_signature, supplied_signature):
            stages["authentication"] = "signature_failed"
            return _record_failure(store, peer_id, started, "authentication", stages)
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            stages["authentication"] = "invalid_response"
            return _record_failure(store, peer_id, started, "authentication", stages)
        if (
            not isinstance(result, dict)
            or result.get("peer_id") != peer["peer_id"]
            or result.get("probe_id") != probe_id
            or result.get("request_nonce") != nonce
        ):
            stages["authentication"] = "identity_mismatch"
            return _record_failure(store, peer_id, started, "authentication", stages)
        stages["authentication"] = "ok"
        elapsed = int((time.monotonic() - started) * 1000)
        result = store.record_network_diagnostic(
            peer_id,
            direction="outbound",
            source="local",
            status="success",
            latency_ms=elapsed,
            checked_at=int(time.time() if now is None else now),
            detail={"stages": stages, "remote_received_at": int(result.get("received_at", 0))},
        )
        return result
    except (socket.gaierror, TimeoutError):
        if stages["tcp"] != "ok":
            stages["tcp"] = "failed"
            category = "tcp_timeout"
        elif parsed.scheme == "https" and stages["tls"] != "ok":
            stages["tls"] = "timeout"
            category = "tls"
        else:
            stages["http"] = "timeout"
            category = "http_timeout"
        return _record_failure(store, peer_id, started, category, stages)
    except OSError as exc:
        category = "tls" if isinstance(exc, ssl.SSLError) else "tcp" if stages["tcp"] != "ok" else "http"
        if category == "tls":
            stages["tls"] = "failed"
        elif category == "tcp":
            stages["tcp"] = "failed"
        else:
            stages["http"] = "failed"
        return _record_failure(store, peer_id, started, category, stages)
    except (http.client.HTTPException, ValueError):
        stages["http"] = "failed"
        return _record_failure(store, peer_id, started, "http", stages)
    finally:
        connection.close()
        if raw_socket is not None:
            raw_socket.close()


def accept_inbound_probe(root: str | Path, request) -> tuple[dict, int]:
    """Validate and persist an authenticated incoming probe; return a signed receipt."""
    store = FederationStore(root)
    supplied_peer_id = str(request.headers.get("X-SimpleOffice-Peer-ID", "")).strip()
    try:
        if not supplied_peer_id or sanitize_peer_id(supplied_peer_id) != supplied_peer_id:
            return {"error": "peer_authentication_failed"}, 401
    except ValueError:
        return {"error": "peer_authentication_failed"}, 401
    try:
        peer_id = str(request.environ.get("simpleoffice.peer_auth:" + str(Path(root).expanduser().resolve())) or "")
        if not peer_id:
            from .federation_peer_auth import authenticate as authenticate_peer
            peer_id = authenticate_peer(root, request)
    except ValueError:
        return {"error": "peer_authentication_failed"}, 401
    if not store.claim_diagnostic_attempt(peer_id, limit=RATE_LIMIT_COUNT, window_seconds=RATE_LIMIT_WINDOW_SECONDS):
        return {"error": "diagnostic_rate_limited"}, 429
    nonce = str(request.headers.get("X-SimpleOffice-Peer-Nonce", ""))
    if not _NONCE_RE.fullmatch(nonce):
        _record_inbound(store, peer_id, status="failure", category="invalid_request", detail={"stages": {"authentication": "ok", "http": "invalid_nonce"}})
        return {"error": "invalid_diagnostic_nonce"}, 400
    if request.content_length is None or request.content_length > 1024:
        _record_inbound(store, peer_id, status="failure", category="invalid_request", detail={"stages": {"authentication": "ok", "http": "invalid_size"}})
        return {"error": "invalid_diagnostic_size"}, 413
    body = request.get_json(silent=True)
    probe_id = str(body.get("probe_id") or "") if isinstance(body, dict) else ""
    if not probe_id or len(probe_id) > 80 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in probe_id):
        _record_inbound(store, peer_id, status="failure", category="invalid_request", detail={"stages": {"authentication": "ok", "http": "invalid_probe"}})
        return {"error": "invalid_diagnostic_probe"}, 400
    timestamp = int(time.time())
    token = store.peer_token(peer_id)
    payload = _json_bytes({
        "peer_id": local_peer_id(), "probe_id": probe_id,
        "request_nonce": nonce, "received_at": timestamp,
    })
    signature = response_signature(token, nonce, payload)
    _record_inbound(
        store, peer_id, status="success", category="authenticated_probe",
        detail={"stages": {"authentication": "ok", "http": "accepted"}}, checked_at=timestamp,
    )
    return {
        "body": payload,
        "signature": signature,
    }, 200
