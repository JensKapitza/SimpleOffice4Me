"""Bounded OpenAI transport. DNS addresses are checked and pinned per connection."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import queue
import threading
import ssl
import time
from urllib.parse import urlsplit

from .litellm_config import gateway_url, secret, settings

MAX_BYTES = 1024 * 1024
_RESOLVERS = threading.BoundedSemaphore(4)


class GatewayError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _resolve(host, port, timeout):
    resolver = _RESOLVERS
    if not resolver.acquire(blocking=False):
        raise GatewayError("resolver_busy")
    results = queue.Queue(maxsize=1)

    def run():
        try:
            results.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            results.put(exc)
        finally:
            resolver.release()

    threading.Thread(target=run, name="litellm-dns", daemon=True).start()
    try:
        result = results.get(timeout=timeout)
    except queue.Empty as exc:
        raise GatewayError("timeout") from exc
    if isinstance(result, OSError):
        raise result
    return result


def addresses(config, host, port, timeout=10):
    if config["mode"] == "local":
        return [(socket.AF_INET, ("127.0.0.1", port))]
    networks = [ipaddress.ip_network(n) for n in config["allowed_networks"]]
    result = []
    for family, _, _, _, address in _resolve(host, port, timeout):
        ip = ipaddress.ip_address(address[0])
        if (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or ip.is_reserved or getattr(ip, "ipv4_mapped", None)
                or not (ip.is_global or any(ip in network for network in networks))):
            raise GatewayError("endpoint_blocked")
        result.append((family, address))
    if not result:
        raise GatewayError("unreachable")
    return result


def _exchange(config, path, body, timeout):
    parsed = urlsplit(gateway_url(config))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.monotonic() + timeout
    targets = addresses(config, parsed.hostname, port, timeout)
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise GatewayError("timeout")
    # Never perform a second hostname lookup; preserve TLS hostname verification.
    family, address = targets[0]
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    sock = socket.socket(family, socket.SOCK_STREAM)
    sockets = [sock]

    def interrupt():
        try:
            sockets[0].shutdown(socket.SHUT_RDWR)
        except OSError:
            return

    timer = threading.Timer(timeout, interrupt)
    timer.daemon = True
    timer.start()
    try:
        sock.settimeout(timeout)
        sock.connect(address)
        if parsed.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
            sockets[0] = sock
        connection.sock = sock
        raw = None if body is None else json.dumps(body).encode()
        if raw is not None and len(raw) > MAX_BYTES:
            raise GatewayError("request_too_large")
        connection.request("GET" if raw is None else "POST", path, body=raw,
                           headers={"Authorization": "Bearer " + secret(config, "api_key"),
                                    "Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read(MAX_BYTES + 1)
        if time.monotonic() >= deadline:
            raise GatewayError("timeout")
        if len(data) > MAX_BYTES:
            raise GatewayError("response_too_large")
        if response.status in (401, 403):
            raise GatewayError("unauthorized")
        if response.status == 429 or response.status >= 500:
            raise GatewayError("temporarily_unavailable")
        if response.status != 200:
            raise GatewayError("gateway_rejected")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise GatewayError("invalid_response") from exc
        if path == "/health/liveliness" and value == "I'm alive!":
            return {"status": "alive"}
        if not isinstance(value, dict):
            raise GatewayError("invalid_response")
        return value
    finally:
        timer.cancel()
        connection.close()
        sock.close()


def request_gateway(path, body=None, *, config=None):
    config = config or settings()
    if not config["enabled"]:
        raise GatewayError("disabled")
    if path not in {"/health/liveliness", "/health/readiness", "/v1/models", "/v1/chat/completions"}:
        raise ValueError("Unbekannte Gateway-Operation.")
    deadline = time.monotonic() + config["timeout"]
    # Retry only idempotent checks, never billable/model POST requests.
    attempts = config["retries"] + 1 if body is None else 1
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GatewayError("timeout")
        try:
            return _exchange(config, path, body, remaining)
        except (OSError, http.client.HTTPException) as exc:
            error = GatewayError("timeout" if isinstance(exc, TimeoutError) else "unreachable")
        except GatewayError as exc:
            if exc.code != "temporarily_unavailable":
                raise
            error = exc
        if attempt + 1 == attempts:
            raise error
        time.sleep(min(.2 * (attempt + 1), max(0, deadline - time.monotonic())))
    raise GatewayError("unreachable")


def completion(messages, *, max_tokens=256):
    config = settings()
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 100 or type(max_tokens) is not int
            or not 1 <= max_tokens <= 4096):
        raise ValueError("Nachrichten oder Tokenlimit prüfen.")
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message["content"], str)):
            raise ValueError("Ungültige Nachricht.")
    return request_gateway("/v1/chat/completions", {"model": config["model"], "messages": messages,
                           "max_tokens": max_tokens, "stream": False}, config=config)


def probe(config=None):
    config = config or settings()
    if not config["enabled"]:
        return {"ok": False, "code": "disabled", "message": "LiteLLM deaktiviert."}
    try:
        deadline = time.monotonic() + config["timeout"]
        models = {}
        for path in ("/health/liveliness", "/health/readiness", "/v1/models"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GatewayError("timeout")
            models = request_gateway(path, config={**config, "timeout": remaining})
        if not isinstance(models.get("data"), list) or not any(
                isinstance(row, dict) and row.get("id") == config["model"] for row in models["data"]):
            raise GatewayError("model_missing")
        return {"ok": True, "code": "ready", "message": "Gateway bereit; Key und Standardmodell geprüft."}
    except GatewayError as exc:
        return {"ok": False, "code": exc.code, "message": "Gateway-Prüfung fehlgeschlagen: " + exc.code}
