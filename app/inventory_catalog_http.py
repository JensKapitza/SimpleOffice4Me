"""Bounded HTTPS transport for the explicit scanner catalog."""
from __future__ import annotations

import http.client
import json
import ssl
import time
from typing import Any
from urllib.parse import urlsplit

TIMEOUT_SECONDS = 3
MAX_BYTES = 256 * 1024
_ALLOWED = {"openlibrary.org": {"/api/books"}, "www.googleapis.com": {"/books/v1/volumes"}}


def get_json(host: str, path: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    target = urlsplit(path)
    if (target.scheme or target.netloc or target.fragment
            or target.path not in _ALLOWED.get(host, ())):
        raise ValueError("Unzulässige Katalogquelle")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    connection = http.client.HTTPSConnection(host, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
    try:
        connection.request("GET", path, headers={"Accept": "application/json",
                                                "User-Agent": "SimpleOffice4Me inventory catalog",
                                                **(headers or {})})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Katalogquelle nicht verfügbar")
        declared = response.getheader("Content-Length")
        if declared is not None and not 0 <= int(declared) <= MAX_BYTES:
            raise ValueError("Katalogantwort zu groß")
        data = bytearray()
        while len(data) <= MAX_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Katalogzeitlimit erreicht")
            # The socket may have transferred to HTTPResponse when Connection: close.
            reader = response.fp
            sock = connection.sock or getattr(getattr(reader, "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(min(16384, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise ValueError("Katalogantwort zu groß")
    finally:
        connection.close()
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("Ungültige Katalogantwort")
    return payload
