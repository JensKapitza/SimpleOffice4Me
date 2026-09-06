"""Browser integration primitives for the SimpleOffice password vault.

Native Messaging is the preferred direct system integration for Chrome/Chromium
and Firefox. This module only defines manifests, framing and URL matching; it
does not persist an unlocked vault key. A future local vault agent owns the
short-lived in-memory unlock state.
"""
from __future__ import annotations

import io
import json
import re
import struct
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

HOST_NAME = "com.simpleoffice.passwords"
MAX_NATIVE_MESSAGE = 1024 * 1024
CHROME_EXTENSION_RE = re.compile(r"^[a-p]{32}$")
FIREFOX_EXTENSION_RE = re.compile(r"^[A-Za-z0-9._@{}+-]{3,160}$")


def native_host_manifest(browser: str, extension_id: str, executable: str | Path) -> dict[str, object]:
    """Return an official Chrome/Firefox Native Messaging host manifest."""
    browser = str(browser).strip().casefold()
    extension_id = str(extension_id).strip()
    executable_path = Path(executable).expanduser().resolve()
    if not executable_path.is_absolute():
        raise ValueError("Native-Messaging-Host benötigt einen absoluten Pfad")
    base: dict[str, object] = {
        "name": HOST_NAME,
        "description": "SimpleOffice4Me password vault browser bridge",
        "path": str(executable_path),
        "type": "stdio",
    }
    if browser in {"chrome", "chromium", "edge", "brave"}:
        if not CHROME_EXTENSION_RE.fullmatch(extension_id):
            raise ValueError("Ungültige Chromium-Erweiterungs-ID")
        base["allowed_origins"] = [f"chrome-extension://{extension_id}/"]
        return base
    if browser == "firefox":
        if not FIREFOX_EXTENSION_RE.fullmatch(extension_id):
            raise ValueError("Ungültige Firefox-Erweiterungs-ID")
        base["allowed_extensions"] = [extension_id]
        return base
    raise ValueError("Nicht unterstützter Browser")


def encode_native_message(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_NATIVE_MESSAGE:
        raise ValueError("Native-Messaging-Nachricht ist zu groß")
    return struct.pack("=I", len(raw)) + raw


def read_native_message(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Unvollständiger Native-Messaging-Header")
    size = struct.unpack("=I", header)[0]
    if size < 2 or size > MAX_NATIVE_MESSAGE:
        raise ValueError("Ungültige Native-Messaging-Nachrichtengröße")
    raw = stream.read(size)
    if len(raw) != size:
        raise ValueError("Unvollständige Native-Messaging-Nachricht")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Native-Messaging-Nachricht ist kein gültiges JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Native-Messaging-Nachricht muss ein Objekt sein")
    return payload


def decode_native_message(data: bytes) -> dict[str, Any]:
    stream = io.BytesIO(data)
    payload = read_native_message(stream)
    if payload is None or stream.read(1):
        raise ValueError("Ungültiger Native-Messaging-Frame")
    return payload


def normalized_origin(value: str) -> tuple[str, str, int | None] | None:
    """Normalize HTTP(S) URL for login matching without exposing path/query data."""
    try:
        parsed = urlparse(str(value).strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.casefold().rstrip("."), port


def login_matches_url(item: dict[str, Any], page_url: str) -> bool:
    """Conservative same-host match used before an extension may offer autofill."""
    if str(item.get("type") or "login") != "login":
        return False
    page = normalized_origin(page_url)
    target = normalized_origin(str(item.get("url") or ""))
    if page is None or target is None:
        return False
    page_scheme, page_host, page_port = page
    target_scheme, target_host, target_port = target
    if page_host != target_host or page_port != target_port:
        return False
    # Never downgrade an HTTPS credential to an HTTP page.
    if target_scheme == "https" and page_scheme != "https":
        return False
    return True


def matching_logins(entries: list[dict[str, Any]], page_url: str) -> list[dict[str, Any]]:
    """Return only minimal browser-facing login fields for a matched origin."""
    result = []
    for wrapper in entries:
        data = wrapper.get("data") if isinstance(wrapper, dict) else None
        if not isinstance(data, dict) or not login_matches_url(data, page_url):
            continue
        result.append({
            "entry_id": str(wrapper.get("entry_id") or ""),
            "name": str(data.get("name") or "")[:500],
            "username": str(data.get("username") or "")[:5000],
            "password": str(data.get("password") or "")[:200_000],
            "url": str(data.get("url") or "")[:10_000],
            "totp": str(data.get("totp") or "")[:20_000],
        })
    return result


def browser_integration_capabilities() -> dict[str, object]:
    return {
        "native_messaging": {
            "host": HOST_NAME,
            "browsers": ["chrome", "chromium", "edge", "brave", "firefox"],
            "autofill": True,
            "save_update": True,
            "requires_unlocked_local_agent": True,
        },
        "bitwarden_compatible_server": {
            "status": "planned",
            "reason": "Protocol adapter must be versioned and tested against official clients before being advertised as compatible.",
        },
        "direct_browser_database_write": {
            "status": "disabled",
            "reason": "Browser-internal password stores are not treated as a stable public write API.",
        },
    }
