"""Compatible endpoint transport sharing LiteLLM's DNS pinning and TLS connector."""
from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from ..litellm_gateway import GatewayError, _connect, addresses
from .vision import VisionError

MAX_RESPONSE_BYTES = 256 * 1024
LOCAL_ENDPOINTS = frozenset({"127.0.0.1", "localhost", "::1"})


def validate_endpoint(value: object) -> str:
    """Accept explicit HTTPS or loopback HTTP, without credentials or URL parameters."""
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() or ord(c) < 32 for c in value):
        raise VisionError("Anbieter-URL ist ungültig.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise VisionError("Anbieter-URL ist ungültig.") from None
    if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
            or port == 0 or "\\" in value or "%" in value
            or not re.fullmatch(r"[/A-Za-z0-9._~-]*", parts.path)
            or any(segment in {".", ".."} for segment in parts.path.split("/"))):
        raise VisionError("Anbieter-URL ohne Zugangsdaten, Query oder relative Pfade erforderlich.")
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOCAL_ENDPOINTS):
        raise VisionError("Anbieter benötigt HTTPS; HTTP ist nur für Loopback erlaubt.")
    return value.rstrip("/")


def is_local_endpoint(endpoint: str) -> bool:
    return urlsplit(validate_endpoint(endpoint)).hostname in LOCAL_ENDPOINTS


def request_json(endpoint: str, key: str, path: str, body: dict | None, timeout: float = 60) -> dict:
    """Bounded request with pinned DNS, verified TLS, no redirects or POST retries."""
    parsed = urlsplit(validate_endpoint(endpoint))
    if path not in {"/models", "/chat/completions"}:
        raise VisionError("Unbekannte Anbieter-Operation.")
    if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in key):
        raise VisionError("Anbieter-Key ist ungültig.")
    limit = max(1, min(float(timeout), 60))
    deadline = time.monotonic() + limit
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sockets = [None]
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=limit)

    def interrupt():
        if sockets[0] is not None:
            try:
                sockets[0].shutdown(socket.SHUT_RDWR)
            except OSError:
                return

    timer = threading.Timer(limit, interrupt)
    timer.daemon = True
    timer.start()
    try:
        if parsed.hostname in LOCAL_ENDPOINTS:
            targets = [(socket.AF_INET6, ("::1", port, 0, 0))] if parsed.hostname == "::1" else [(socket.AF_INET, ("127.0.0.1", port))]
        else:
            targets = addresses({"mode": "external", "allowed_networks": []}, parsed.hostname, port, limit)
        connection.sock = _connect(parsed, targets, deadline, sockets)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        if payload is not None and len(payload) > 8 * 1024 * 1024:
            raise VisionError("Anbieter-Anfrage ist zu groß.")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        connection.request("GET" if body is None else "POST", parsed.path.rstrip("/") + path,
                           body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if time.monotonic() >= deadline:
            raise VisionError("Anbieter-Zeitlimit überschritten.")
        if response.status != 200:
            raise VisionError(f"Anbieter-Anfrage fehlgeschlagen (HTTP {response.status}).")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise VisionError("Anbieter-Antwort ist zu groß.")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise VisionError("Anbieter antwortete nicht mit einem JSON-Objekt.")
        return result
    except VisionError:
        raise
    except (OSError, ValueError, RecursionError, http.client.HTTPException, GatewayError):
        raise VisionError("Anbieter nicht erreichbar oder Antwort ungültig; URL, TLS und Dienst prüfen.") from None
    finally:
        timer.cancel()
        connection.close()
        if sockets[0] is not None:
            sockets[0].close()
