"""No-install interoperability helpers for the SimpleOffice password vault.

A normal web/PWA origin cannot inject passwords into arbitrary third-party pages
or write directly to Chrome/Firefox internal password databases.  Therefore this
module deliberately offers only portable import/export helpers and capability
metadata.  Live browser integration is reserved for already-installed compatible
clients (for example a future Bitwarden protocol adapter) and is never presented
as a built-in-browser capability.
"""
from __future__ import annotations

import csv
import io
from typing import Any
from urllib.parse import urlparse

MAX_CSV_BYTES = 16 * 1024 * 1024
MAX_ROWS = 20_000
MAX_FIELD = 200_000


def normalized_http_url(value: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 10_000:
        raise ValueError("Ungültige URL")
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise ValueError("Ungültige URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Nur HTTP(S)-URLs ohne eingebettete Zugangsdaten sind erlaubt")
    return value


def browser_csv_export(entries: list[dict[str, Any]]) -> bytes:
    """Create a transient Chrome/Google/Firefox-compatible CSV payload.

    The returned bytes contain plaintext credentials by definition.  Callers must
    deliver them as an explicit one-shot download with no-store headers and should
    never persist the generated CSV inside the document tree or audit log.
    """
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["url", "username", "password"], lineterminator="\n")
    writer.writeheader()
    count = 0
    for wrapper in entries:
        data = wrapper.get("data") if isinstance(wrapper, dict) else None
        if not isinstance(data, dict) or str(data.get("type") or "login") != "login":
            continue
        try:
            url = normalized_http_url(str(data.get("url") or ""))
        except ValueError:
            continue
        username = str(data.get("username") or "")
        password = str(data.get("password") or "")
        if len(username) > MAX_FIELD or len(password) > MAX_FIELD:
            raise ValueError("Zugangsdatenfeld ist zu groß")
        writer.writerow({"url": url, "username": username, "password": password})
        count += 1
        if count > MAX_ROWS:
            raise ValueError("Zu viele Passworteinträge für einen CSV-Export")
    raw = output.getvalue().encode("utf-8")
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError("Passwort-CSV ist zu groß")
    return raw


def browser_csv_import(data: bytes) -> list[dict[str, str]]:
    """Parse a browser password CSV into review-only login candidates.

    Import only parses and validates.  It never writes to the vault on its own,
    so the UI can show duplicates/conflicts before encrypted storage.
    """
    if not data or len(data) > MAX_CSV_BYTES:
        raise ValueError("Ungültige Passwort-CSV-Größe")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Passwort-CSV muss UTF-8 sein") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = {str(field or "").strip().casefold() for field in (reader.fieldnames or [])}
    if not {"url", "username", "password"}.issubset(fields):
        raise ValueError("CSV benötigt die Spalten url, username und password")
    lookup = {str(field or "").strip().casefold(): str(field) for field in (reader.fieldnames or [])}
    result: list[dict[str, str]] = []
    for index, row in enumerate(reader, start=1):
        if index > MAX_ROWS:
            raise ValueError("Zu viele Passwortzeilen")
        url_raw = str(row.get(lookup["url"], "") or "").strip()
        username = str(row.get(lookup["username"], "") or "")
        password = str(row.get(lookup["password"], "") or "")
        if not url_raw and not username and not password:
            continue
        url = normalized_http_url(url_raw)
        if len(username) > MAX_FIELD or len(password) > MAX_FIELD:
            raise ValueError("Zugangsdatenfeld ist zu groß")
        result.append({
            "type": "login",
            "name": urlparse(url).hostname or url,
            "url": url,
            "username": username,
            "password": password,
        })
    return result


def no_install_browser_capabilities() -> dict[str, object]:
    """Truthful capability matrix for a browser-only/PWA installation."""
    return {
        "pwa_vault": {
            "available": True,
            "manage_entries": True,
            "copy_reveal": True,
            "encrypted_backup_restore": True,
            "third_party_site_autofill": False,
            "reason": "A PWA is sandboxed to its own origin and cannot fill arbitrary third-party pages.",
        },
        "built_in_chrome_password_manager": {
            "live_sync_from_simpleoffice": False,
            "csv_import_export": True,
            "server_endpoint_configurable": False,
        },
        "built_in_firefox_password_manager": {
            "live_sync_from_simpleoffice": False,
            "csv_import_export": True,
            "server_endpoint_configurable": False,
        },
        "compatible_existing_client": {
            "bitwarden_protocol_adapter": "planned",
            "requires_client_already_installed": True,
            "simpleoffice_extension_required": False,
        },
        "direct_browser_database_write": {
            "enabled": False,
            "reason": "Internal browser password stores are not a stable public write API.",
        },
    }
