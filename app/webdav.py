"""Writable, versioned WebDAV endpoint for LibreOffice remote editing."""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .attachment_security import AttachmentSecurity, QuarantineCapacityError
from .document_store import (
    CONTROL_DIR,
    HISTORY_DIR,
    MAX_WEBDAV_COLLECTION_DEPTH,
    MAX_WEBDAV_COLLECTION_MEMBERS,
    POLICY_FILE,
    DocumentStore,
    atomic_json_write,
    sha256_file,
    utc_now,
)
from .file_lock import exclusive_file_lock


bp = Blueprint("webdav", __name__)
DAV = "DAV:"
MICROSOFT_DAV = "urn:schemas-microsoft-com:"
MICROSOFT_OFFICE = "urn:schemas-microsoft-com:office:office"
ElementTree.register_namespace("Z", MICROSOFT_DAV)
ElementTree.register_namespace("Office", MICROSOFT_OFFICE)


def _release_mutation_lock() -> None:
    context = g.pop("_webdav_mutation_lock", None)
    if context is not None:
        context.__exit__(None, None, None)


@bp.after_request
def _after_webdav_request(response: Response) -> Response:
    _release_mutation_lock()
    return response


@bp.teardown_request
def _teardown_webdav_request(_error: BaseException | None) -> None:
    _release_mutation_lock()


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _credentials_path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "webdav-credentials.json"


def _credential_usage_path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "webdav-credential-usage.json"


def _locks_path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "webdav-locks.json"


def _sync_path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "webdav-sync.json"


def _properties_path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "webdav-properties.json"


def _read_json(path: Path, fallback: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except (OSError, json.JSONDecodeError):
        return fallback


MAX_ACTIVE_CREDENTIALS = 10
CREDENTIAL_USAGE_WRITE_INTERVAL_SECONDS = 15 * 60
_credential_usage_cache: dict[tuple[str, str, str], float] = {}
_credential_usage_cache_lock = threading.Lock()
WRITE_METHODS = {"PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK", "PROPPATCH"}
MAX_SYNC_CHANGES = 4096
MAX_SYNC_TOKENS = 512
MAX_SYNC_PAGE_RESULTS = 500
MAX_PROPERTY_BODY = 64 * 1024
MAX_PROPERTY_COUNT = 64
MAX_STORED_PROPERTIES = 128
MAX_PROPERTY_VALUE = 16 * 1024
MAX_PROPERTY_NODES = 256
MAX_BYTE_RANGES = 8
DOWNLOAD_CHUNK_SIZE = 64 * 1024
MAX_DIGEST_FIELD_BYTES = 2048
MAX_HTTP_PRECONDITION_BYTES = 8192
MAX_HTTP_PRECONDITION_TAGS = 64
MAX_IF_HEADER_BYTES = 16 * 1024
MAX_IF_LISTS = 64
MAX_IF_CONDITIONS = 256
MAX_PROPFIND_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_SEARCH_RESULTS = 500
MAX_SEARCH_ORDERS = 8
MAX_SEARCH_OPERATORS = 64
MAX_SEARCH_EXPRESSION_DEPTH = 16
DIGEST_ALGORITHMS = {
    "sha-256": (hashlib.sha256, 32, 10),
    "sha-512": (hashlib.sha512, 64, 9),
}
DIGEST_PREFERENCE = "sha-512=9, sha-256=10"
PROTECTED_DAV_PROPERTIES = {
    f"{{{DAV}}}{name}" for name in (
        "alternate-URI-set", "creationdate", "current-user-principal",
        "current-user-privilege-set", "getcontentlength",
        "getcontenttype", "getetag", "getlastmodified", "lockdiscovery",
        "group-membership", "owner", "principal-collection-set", "principal-URL",
        "quota-available-bytes", "quota-used-bytes", "resourcetype",
        "supportedlock", "supported-method-set", "supported-query-grammar-set",
        "supported-report-set", "sync-token",
    )
}
MUTABLE_DAV_PROPERTIES = {f"{{{DAV}}}displayname", f"{{{DAV}}}getcontentlanguage"}
MICROSOFT_CLIENT_PROPERTIES = {
    f"{{{MICROSOFT_DAV}}}{name}" for name in (
        "Win32FileAttributes", "Win32CreationTime",
        "Win32LastAccessTime", "Win32LastModifiedTime",
    )
}
MICROSOFT_SPECIAL_FOLDER = f"{{{MICROSOFT_OFFICE}}}specialFolderType"
WINDOWS_RESERVED_BASENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in "¹²³"),
    *(f"LPT{number}" for number in "¹²³"),
}
WINDOWS_FORBIDDEN_NAME_CHARACTERS = frozenset('<>:"/\\|?*')
BIDI_CONTROL_CHARACTERS = frozenset(
    chr(codepoint)
    for codepoint in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F)
)
# Keep headroom for the exclusive ``.<name>.<uuid>.partial`` staging member
# used by atomic PUT/COPY writes on filesystems with 255-byte segments.
MAX_PORTABLE_NAME_BYTES = 200
PROPFIND_XML_PREFIX = '<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">'
PROPFIND_XML_SUFFIX = "</d:multistatus>"


class _PropfindLimitError(Exception):
    """Signal a bounded recursive listing that cannot be returned safely."""

    def __init__(self, reason: str, observed: int, limit: int):
        super().__init__(reason)
        self.reason = reason
        self.observed = observed
        self.limit = limit


class _SearchError(Exception):
    """Map a bounded RFC 5323 parsing or capability failure to DAV XML."""

    def __init__(self, status: int, message: str, condition: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.condition = condition


def _quota_state() -> dict[str, int] | None:
    """Return repeatable RFC 4331 accounting for the visible managed tree."""
    if hasattr(g, "_webdav_quota_state"):
        return g._webdav_quota_state
    limit = max(0, int(current_app.config.get("WEBDAV_QUOTA_BYTES", 0)))
    if not limit:
        g._webdav_quota_state = None
        return None
    root = _store().root
    used = 0
    for current, directories, files in os.walk(root, followlinks=False):
        parent = Path(current)
        directories[:] = [
            name for name in directories
            if name not in {CONTROL_DIR, HISTORY_DIR} and not (parent / name).is_symlink()
        ]
        for name in files:
            if name == POLICY_FILE:
                continue
            path = parent / name
            try:
                if path.is_file() and not path.is_symlink():
                    used += path.stat().st_size
            except OSError:
                continue
    try:
        physical_free = max(0, int(shutil.disk_usage(root).free))
    except OSError:
        physical_free = max(0, limit - used)
    state = {
        "limit": limit,
        "used": used,
        "available": min(max(0, limit - used), physical_free),
        "physical_free": physical_free,
    }
    g._webdav_quota_state = state
    return state


def _quota_error(username: str, operation: str, resource: Path, growth: int, condition: str) -> Response:
    state = _quota_state() or {"limit": 0, "used": 0, "available": 0}
    _store().history.record(
        "webdav_quota_rejected",
        f"webdav:{username}",
        "webdav-quota",
        hashlib.sha256(f"{username}:{operation}:{_store().relative(resource)}".encode()).hexdigest(),
        {
            "operation": operation,
            "resource": _store().relative(resource),
            "requested_growth": max(0, growth),
            "used": state["used"],
            "limit": state["limit"],
            "rejected_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )
    xml = f'<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:{condition}/></d:error>'
    return Response(xml, 507, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})


def _webdav_upload_scan_error(content: bytes, username: str, resource: Path) -> Response | None:
    """Fail closed before a PUT body becomes a managed document revision."""
    if not current_app.config.get("WEBDAV_UPLOAD_SCAN", False):
        return None
    try:
        result = AttachmentSecurity(current_app.config["DOCUMENT_ROOT"]).scan_webdav_upload(
            content,
            f"webdav:{username}",
            _store().relative(resource),
            max(1, int(current_app.config["WEBDAV_QUARANTINE_BYTES"])),
        )
    except QuarantineCapacityError:
        xml = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:sufficient-disk-space/></d:error>'
        return Response(xml, 507, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})
    except (OSError, RuntimeError, ValueError):
        return Response(
            "malware scanner unavailable; upload was not published",
            503,
            {"Retry-After": "60", "Cache-Control": "no-store"},
        )
    if result.get("verdict") != "clean":
        return Response(
            "malware detected; upload was quarantined and not published",
            422,
            {"Cache-Control": "no-store"},
        )
    return None


def _check_quota(username: str, operation: str, resource: Path, growth: int) -> Response | None:
    """Reject positive allocation growth before mutation while its lock is held."""
    if growth <= 0:
        return None
    state = _quota_state()
    if state is None:
        return None
    if growth > state["physical_free"]:
        return _quota_error(username, operation, resource, growth, "sufficient-disk-space")
    if growth > state["available"]:
        return _quota_error(username, operation, resource, growth, "quota-not-exceeded")
    return None


def _credential_records(value: object) -> list[dict]:
    """Read both the legacy single-password record and the v2 device list."""
    if not isinstance(value, dict):
        return []
    if "salt" in value and "hash" in value:
        return [{
            **value,
            "credential_id": "legacy",
            "label": "Bestehender Desktop-Zugang",
            "scope": "write",
            "path_prefix": "",
            "expires_at": "",
        }]
    records = value.get("credentials", [])
    return [dict(record) for record in records if isinstance(record, dict)] if isinstance(records, list) else []


def _expired(record: dict, now: datetime | None = None) -> bool:
    expires_at = str(record.get("expires_at", "")).strip()
    if not expires_at:
        return False
    try:
        expires = datetime.fromisoformat(expires_at).astimezone(timezone.utc)
    except ValueError:
        return True
    return expires <= (now or datetime.now(timezone.utc))


def _nonnegative_int(value: object) -> int:
    """Treat corrupt optional counters as absent instead of breaking settings."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def credentials_for(username: str) -> list[dict]:
    """Return display-safe credential metadata without password material."""
    users = _read_json(_credentials_path(), {"users": {}}).get("users", {})
    value = users.get(username) if isinstance(users, dict) else None
    usage_users = _read_json(_credential_usage_path(), {"users": {}}).get("users", {})
    usage = usage_users.get(username, {}) if isinstance(usage_users, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    result = []
    for record in _credential_records(value):
        credential_id = str(record.get("credential_id", ""))
        last_use = usage.get(credential_id, {})
        if not isinstance(last_use, dict):
            last_use = {}
        result.append({
            "credential_id": credential_id,
            "label": str(record.get("label", "Desktop-Zugang")),
            "scope": "read" if record.get("scope") == "read" else "write",
            "path_prefix": str(record.get("path_prefix", "")).strip(),
            "created_at": str(record.get("created_at", "")),
            "expires_at": str(record.get("expires_at", "")),
            "expired": _expired(record),
            "rotated_at": str(record.get("rotated_at", "")),
            "rotation_count": _nonnegative_int(record.get("rotation_count", 0)),
            "last_used_at": str(last_use.get("last_used_at", "")),
            "last_method": str(last_use.get("method", "")),
            "last_client": str(last_use.get("client", "")),
        })
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def _client_family(user_agent: str) -> str:
    """Reduce a User-Agent to a small, non-identifying interoperability label."""
    value = user_agent.casefold()
    for marker, label in (
        ("libreoffice", "LibreOffice"),
        ("freefilesync", "FreeFileSync"),
        ("microsoft-webdav-miniredir", "Windows Explorer"),
        ("webdavfs", "macOS Finder"),
        ("gvfs", "Nautilus/GVfs"),
        ("davfs2", "davfs2"),
    ):
        if marker in value:
            return label
    return "WebDAV-Client"


def _forget_credential_usage(username: str, *credential_ids: str) -> None:
    """Remove stale usage metadata after revoke or rotation."""
    ids = {value for value in credential_ids if value}
    if not ids:
        return
    path = _credential_usage_path()
    try:
        with exclusive_file_lock(path.with_suffix(".lock")):
            payload = _read_json(path, {"version": 1, "users": {}})
            users = payload.get("users")
            if not isinstance(users, dict):
                users = {}; payload["users"] = users
            usage = users.get(username)
            if isinstance(usage, dict):
                for credential_id in ids:
                    usage.pop(credential_id, None)
                if not usage:
                    users.pop(username, None)
            payload["version"] = 1
            atomic_json_write(path, payload)
    except (OSError, RuntimeError, ValueError):
        pass
    root_key = str(_credentials_path())
    with _credential_usage_cache_lock:
        for credential_id in ids:
            _credential_usage_cache.pop((root_key, username, credential_id), None)


def _record_credential_use(username: str, credential_id: str) -> None:
    """Persist coarse last-use data at most once per 15 minutes and never fail auth."""
    if not credential_id:
        return
    now = datetime.now(timezone.utc)
    cache_key = (str(_credentials_path()), username, credential_id)
    with _credential_usage_cache_lock:
        previous = _credential_usage_cache.get(cache_key, 0.0)
        if now.timestamp() - previous < CREDENTIAL_USAGE_WRITE_INTERVAL_SECONDS:
            return
        _credential_usage_cache[cache_key] = now.timestamp()
    try:
        path = _credential_usage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(path.with_suffix(".lock")):
            payload = _read_json(path, {"version": 1, "users": {}})
            users = payload.get("users")
            if not isinstance(users, dict):
                users = {}; payload["users"] = users
            usage = users.setdefault(username, {})
            if not isinstance(usage, dict):
                usage = {}; users[username] = usage
            current = usage.get(credential_id, {})
            try:
                last_used = datetime.fromisoformat(str(current.get("last_used_at", ""))).astimezone(timezone.utc)
            except (AttributeError, TypeError, ValueError):
                last_used = datetime.min.replace(tzinfo=timezone.utc)
            if (now - last_used).total_seconds() < CREDENTIAL_USAGE_WRITE_INTERVAL_SECONDS:
                return
            usage[credential_id] = {
                "last_used_at": now.isoformat(),
                "method": request.method if request.method in {"OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "SEARCH", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"} else "OTHER",
                "client": _client_family(request.headers.get("User-Agent", "")),
            }
            payload["version"] = 1
            atomic_json_write(path, payload)
    except (OSError, RuntimeError, ValueError):
        # Usage telemetry is deliberately fail-open; authentication and file I/O
        # must never depend on this optional, coarse status projection.
        with _credential_usage_cache_lock:
            _credential_usage_cache.pop(cache_key, None)


def _normalize_credential_prefix(value: str) -> str:
    """Return an existing safe collection relative to the managed root."""
    raw = str(value or "").strip()
    if raw in {"", "."}:
        return ""
    if len(raw) > 500 or any(ord(character) < 32 for character in raw):
        raise ValueError("WebDAV-Ordner darf höchstens 500 druckbare Zeichen enthalten.")
    relative = _store()._safe_managed_relative_path(raw.rstrip("/"), require_name=True)
    collection = _store().root / relative
    if not collection.is_dir() or collection.is_symlink():
        raise ValueError("WebDAV-Ordner muss vorhanden und eine reguläre Sammlung sein.")
    return str(relative)


def _credential_allows_path(identity: dict, resource: Path) -> bool:
    """Apply a credential's collection boundary to existing and new resources."""
    prefix = str(identity.get("path_prefix", "")).strip()
    if not prefix:
        return True
    relative = _store().relative(resource)
    if relative.startswith("[external]") or relative == ".":
        return False
    path = Path(relative)
    boundary = Path(prefix)
    return path == boundary or boundary in path.parents


def _credential_is_boundary(identity: dict, resource: Path) -> bool:
    prefix = str(identity.get("path_prefix", "")).strip()
    return bool(prefix) and _store().relative(resource) == prefix


def activate(
    username: str,
    actor: str,
    *,
    label: str = "Desktop-Zugang",
    scope: str = "write",
    expires_days: int = 90,
    path_prefix: str = "",
) -> str:
    """Create an independently revocable WebDAV app password and return it once."""
    label = " ".join(label.split()).strip()
    if not label or len(label) > 80 or any(ord(character) < 32 for character in label):
        raise ValueError("Bezeichnung muss 1 bis 80 druckbare Zeichen enthalten.")
    if scope not in {"read", "write"}:
        raise ValueError("Unbekannter WebDAV-Rechteumfang.")
    if isinstance(expires_days, bool) or not 1 <= int(expires_days) <= 365:
        raise ValueError("Gültigkeit muss zwischen 1 und 365 Tagen liegen.")
    expires_days = int(expires_days)
    path_prefix = _normalize_credential_prefix(path_prefix)
    credential_id = secrets.token_hex(12)
    password = f"{credential_id}.{secrets.token_urlsafe(24)}"
    salt = os.urandom(16)
    record = {
        "credential_id": credential_id,
        "label": label,
        "scope": scope,
        "path_prefix": path_prefix,
        "salt": salt.hex(),
        "hash": hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex(),
        "created_at": utc_now(),
        "created_by": actor,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat(),
    }
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 2, "users": {}})
        users = payload.get("users")
        if not isinstance(users, dict):
            users = {}
            payload["users"] = users
        records = _credential_records(users.get(username))
        if sum(not _expired(item) for item in records) >= MAX_ACTIVE_CREDENTIALS:
            raise ValueError(f"Höchstens {MAX_ACTIVE_CREDENTIALS} aktive WebDAV-Zugänge sind erlaubt.")
        users[username] = {"credentials": [*records, record]}
        payload["version"] = 2
        atomic_json_write(path, payload)
    _store().history.record(
        "webdav_credential_created", actor, "webdav", hashlib.sha256(username.encode()).hexdigest()[:16],
        {key: record[key] for key in ("credential_id", "label", "scope", "path_prefix", "created_at", "expires_at")},
    )
    return password


def revoke(username: str, actor: str, credential_id: str = "") -> bool:
    """Revoke one device credential, or all credentials when no id is supplied."""
    path = _credentials_path()
    revoked: list[dict] = []
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"users": {}})
        users = payload.get("users")
        if not isinstance(users, dict):
            users = {}
            payload["users"] = users
        records = _credential_records(users.get(username))
        if credential_id:
            revoked = [record for record in records if hmac.compare_digest(str(record.get("credential_id", "")), credential_id)]
            remaining = [record for record in records if record not in revoked]
            if remaining:
                users[username] = {"credentials": remaining}
            else:
                users.pop(username, None)
        else:
            revoked = records
            users.pop(username, None)
        payload["version"] = 2
        atomic_json_write(path, payload)
    for record in revoked:
        _store().history.record(
            "webdav_credential_revoked", actor, "webdav", hashlib.sha256(username.encode()).hexdigest()[:16],
            {"credential_id": record.get("credential_id", ""), "label": record.get("label", ""), "scope": record.get("scope", "write"), "path_prefix": record.get("path_prefix", ""), "revoked_at": utc_now()},
        )
    _forget_credential_usage(username, *(str(record.get("credential_id", "")) for record in revoked))
    return bool(revoked)


def rotate(username: str, actor: str, credential_id: str, expires_days: int = 365) -> str:
    """Atomically replace one app password while preserving its scope and label."""
    if isinstance(expires_days, bool) or not 1 <= int(expires_days) <= 365:
        raise ValueError("Gültigkeit muss zwischen 1 und 365 Tagen liegen.")
    expires_days = int(expires_days)
    path = _credentials_path()
    old_id = credential_id
    rotated: dict = {}
    password = ""
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 2, "users": {}})
        users = payload.get("users")
        if not isinstance(users, dict):
            raise ValueError("WebDAV-Zugang wurde nicht gefunden.")
        records = _credential_records(users.get(username))
        target_index = next((index for index, record in enumerate(records) if hmac.compare_digest(str(record.get("credential_id", "")), credential_id)), None)
        if target_index is None:
            raise ValueError("WebDAV-Zugang wurde nicht gefunden.")
        previous = records[target_index]
        new_id = secrets.token_hex(12) if credential_id == "legacy" else credential_id
        password = f"{new_id}.{secrets.token_urlsafe(24)}"
        salt = os.urandom(16)
        rotated = {
            **previous,
            "credential_id": new_id,
            "salt": salt.hex(),
            "hash": hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat(),
            "rotated_at": utc_now(),
            "rotated_by": actor,
            "rotation_count": _nonnegative_int(previous.get("rotation_count", 0)) + 1,
        }
        records[target_index] = rotated
        users[username] = {"credentials": records}
        payload["version"] = 2
        atomic_json_write(path, payload)
    _forget_credential_usage(username, old_id, str(rotated["credential_id"]))
    _store().history.record(
        "webdav_credential_rotated", actor, "webdav", hashlib.sha256(username.encode()).hexdigest()[:16],
        {key: rotated[key] for key in ("credential_id", "label", "scope", "path_prefix", "expires_at", "rotated_at", "rotation_count")},
    )
    return password


def _authenticate() -> dict | None:
    supplied = request.authorization
    if not supplied or supplied.type.casefold() != "basic" or not supplied.username or not supplied.password:
        return None
    users = _read_json(_credentials_path(), {"users": {}}).get("users", {})
    value = users.get(supplied.username) if isinstance(users, dict) else None
    records = _credential_records(value)
    selector = supplied.password.partition(".")[0] if "." in supplied.password else ""
    if selector:
        selected = [record for record in records if record.get("credential_id") == selector]
        candidates = selected or [record for record in records if record.get("credential_id") == "legacy"]
    else:
        candidates = records
    for record in candidates:
        if _expired(record):
            continue
        try:
            actual = hashlib.scrypt(supplied.password.encode(), salt=bytes.fromhex(record["salt"]), n=2**14, r=8, p=1)
            expected = bytes.fromhex(record["hash"])
        except (KeyError, ValueError):
            continue
        if hmac.compare_digest(actual, expected):
            identity = {
                "username": supplied.username,
                "credential_id": str(record.get("credential_id", "legacy")),
                "scope": "read" if record.get("scope") == "read" else "write",
                "path_prefix": str(record.get("path_prefix", "")).strip(),
            }
            _record_credential_use(identity["username"], identity["credential_id"])
            return identity
    return None


def _unauthorized() -> Response:
    return Response("WebDAV authentication required", 401, {"WWW-Authenticate": 'Basic realm="SimpleOffice4Me Documents", charset="UTF-8"'})


def _need_privileges_response(href: str, privilege: str, allow: str) -> Response:
    """Give ACL-aware clients a useful least-privilege denial without exposing ACLs."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:error xmlns:d="DAV:"><d:need-privileges><d:resource>'
        f'<d:href>{escape(href)}</d:href><d:privilege><d:{privilege}/></d:privilege>'
        '</d:resource></d:need-privileges></d:error>'
    )
    return Response(
        xml,
        403,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Allow": allow,
        },
    )


def _missing_method_privilege(method: str) -> str:
    return {
        "PROPPATCH": "write-properties",
        "UNLOCK": "unlock",
    }.get(method, "write")


def _document_path(document: dict) -> Path:
    path = _store().root / str(document.get("last_path", ""))
    if not path.is_file() or path.is_symlink():
        raise ValueError("document unavailable")
    return path


def _etag(document: dict) -> str:
    path = _document_path(document)
    return f'"{sha256_file(path)}"'


def _stored_integrity_headers(document: dict) -> dict[str, str]:
    """Describe the stored representation after a successful state change."""
    etag = _etag(document)
    digest = bytes.fromhex(_etag_value(etag))
    return {
        "ETag": etag,
        "Repr-Digest": _digest_value("sha-256", digest),
        "Content-Location": request.path,
        "Want-Content-Digest": DIGEST_PREFERENCE,
        "Cache-Control": "private, no-cache",
    }


def _etag_value(value: str) -> str:
    return value.strip().removeprefix("W/").strip('"')


def _http_date_timestamp(value: str) -> int | None:
    """Parse an IMF-fixdate for conditional requests; invalid dates are ignored."""
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def _etag_list_matches(value: str, current_etag: str, *, weak: bool) -> bool:
    try:
        wildcard, tags = _parse_http_etag_list(value)
    except (OverflowError, ValueError):
        return False
    return wildcard or any(
        (weak or not is_weak) and hmac.compare_digest(tag, current_etag)
        for is_weak, tag in tags
    )


def _parse_http_etag_list(value: str) -> tuple[bool, list[tuple[bool, str]]]:
    """Parse a bounded RFC 9110 entity-tag list without splitting quoted commas."""
    if not value:
        raise ValueError("HTTP ETag precondition is empty")
    if len(value.encode("latin-1", errors="replace")) > MAX_HTTP_PRECONDITION_BYTES:
        raise OverflowError("HTTP ETag precondition is too large")
    value = value.strip()
    if value == "*":
        return True, []
    tags: list[tuple[bool, str]] = []
    position = 0
    while position < len(value):
        while position < len(value) and value[position] in " \t":
            position += 1
        weak = value[position:position + 2] == "W/"
        if weak:
            position += 2
        if position >= len(value) or value[position] != '"':
            raise ValueError("HTTP ETag precondition contains an invalid entity-tag")
        start = position
        position += 1
        while position < len(value) and value[position] != '"':
            character = ord(value[position])
            if character != 0x21 and not 0x23 <= character <= 0x7E and not 0x80 <= character <= 0xFF:
                raise ValueError("HTTP ETag precondition contains an invalid entity-tag")
            position += 1
        if position >= len(value):
            raise ValueError("HTTP ETag precondition contains an unterminated entity-tag")
        position += 1
        tags.append((weak, value[start:position]))
        if len(tags) > MAX_HTTP_PRECONDITION_TAGS:
            raise OverflowError("HTTP ETag precondition contains too many entity-tags")
        while position < len(value) and value[position] in " \t":
            position += 1
        if position == len(value):
            break
        if value[position] != ",":
            raise ValueError("HTTP ETag precondition is not a valid list")
        position += 1
        if not value[position:].strip():
            raise ValueError("HTTP ETag precondition contains an empty member")
    return False, tags


def _record_http_precondition_failure(
    username: str, resource: Path, condition: str, status: int, reason: str,
) -> None:
    """Audit rejected mutations without retaining client-supplied validators."""
    relative = _store().relative(resource)
    _store().history.record(
        "webdav_http_precondition_rejected",
        f"webdav:{username}",
        "webdav-preconditions",
        hashlib.sha256(f"{username}:{request.method}:{relative}".encode()).hexdigest(),
        {
            "resource": relative,
            "method": request.method,
            "condition": condition,
            "status": status,
            "reason": reason,
            "rejected_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _http_precondition_error(username: str, resource: Path, document: dict | None) -> Response | None:
    """Evaluate unsafe-request HTTP preconditions in RFC 9110 precedence order."""
    exists = (document is not None and resource.is_file() and not resource.is_symlink()) or (
        resource.is_dir() and not resource.is_symlink()
    )
    current_etag = _etag(document) if document is not None and exists else ""
    modified_at: int | None = None
    headers = {"Cache-Control": "private, no-cache"}
    if exists:
        try:
            modified_at = int(resource.stat().st_mtime)
            headers["Last-Modified"] = formatdate(modified_at, usegmt=True)
        except OSError:
            modified_at = None
    if current_etag:
        headers["ETag"] = current_etag

    def reject(condition: str, reason: str, status: int = 412) -> Response:
        _record_http_precondition_failure(username, resource, condition, status, reason)
        return Response(reason, status, headers)

    if_match = request.headers.get("If-Match")
    if if_match is not None:
        try:
            wildcard, tags = _parse_http_etag_list(if_match)
        except OverflowError as exc:
            return reject("If-Match", str(exc), 413)
        except ValueError as exc:
            return reject("If-Match", str(exc), 400)
        matches = exists and (
            wildcard or bool(current_etag) and any(
                not weak and hmac.compare_digest(tag, current_etag) for weak, tag in tags
            )
        )
        if not matches:
            return reject("If-Match", "If-Match precondition failed")
    else:
        unmodified = request.headers.get("If-Unmodified-Since")
        unmodified_at = _http_date_timestamp(unmodified) if unmodified else None
        if unmodified_at is not None and modified_at is not None and modified_at > unmodified_at:
            return reject("If-Unmodified-Since", "If-Unmodified-Since precondition failed")

    if_none_match = request.headers.get("If-None-Match")
    if if_none_match is not None:
        try:
            wildcard, tags = _parse_http_etag_list(if_none_match)
        except OverflowError as exc:
            return reject("If-None-Match", str(exc), 413)
        except ValueError as exc:
            return reject("If-None-Match", str(exc), 400)
        matches = exists and (
            wildcard or bool(current_etag) and any(
                hmac.compare_digest(tag, current_etag) for _weak, tag in tags
            )
        )
        if matches:
            return reject("If-None-Match", "If-None-Match precondition failed")
    return None


def _digest_value(algorithm: str, digest: bytes) -> str:
    """Serialize an RFC 9530 digest as an RFC 8941 Byte Sequence."""
    return f"{algorithm}=:{base64.b64encode(digest).decode('ascii')}:"


def _parse_digest_field(value: str) -> dict[str, bytes]:
    """Parse the supported subset of the RFC 9530 Structured Field dictionary.

    Digest algorithms use byte-sequence values. Parameters, duplicate keys and
    malformed Base64 are rejected instead of being interpreted ambiguously.
    Unsupported algorithms are ignored only when at least one supported active
    algorithm can be verified.
    """
    if not value or len(value.encode("utf-8")) > MAX_DIGEST_FIELD_BYTES:
        raise ValueError("Content-Digest is empty or too large")
    parsed: dict[str, bytes] = {}
    saw_member = False
    for raw_member in value.split(","):
        member = raw_member.strip()
        if not member:
            raise ValueError("Content-Digest contains an empty member")
        key, separator, encoded = member.partition("=")
        key = key.strip().casefold()
        saw_member = True
        if separator != "=" or not re.fullmatch(r"[a-z*][a-z0-9_.*-]*", key):
            raise ValueError("Content-Digest is not a valid dictionary")
        if key in parsed:
            raise ValueError("Content-Digest contains a duplicate algorithm")
        if key not in DIGEST_ALGORITHMS:
            continue
        encoded = encoded.strip()
        if ";" in encoded or len(encoded) < 2 or encoded[0] != ":" or encoded[-1] != ":":
            raise ValueError("Content-Digest requires a byte-sequence value")
        try:
            decoded = base64.b64decode(encoded[1:-1], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Content-Digest contains invalid Base64") from None
        expected_length = DIGEST_ALGORITHMS[key][1]
        if len(decoded) != expected_length:
            raise ValueError(f"Content-Digest {key} has the wrong length")
        parsed[key] = decoded
    if not saw_member or not parsed:
        raise ValueError("Content-Digest has no supported active algorithm")
    return parsed


def _digest_audit(username: str, resource: Path, action: str, algorithms: list[str], size: int) -> None:
    """Record integrity decisions without copying client-supplied digest values."""
    relative = _store().relative(resource)
    _store().history.record(
        action,
        f"webdav:{username}",
        "webdav-integrity",
        hashlib.sha256(f"{username}:{relative}".encode()).hexdigest(),
        {
            "resource": relative,
            "algorithms": sorted(algorithms),
            "size": size,
            "checked_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _verify_content_digest(content: bytes, username: str, resource: Path) -> Response | None:
    """Fail a PUT before quota checks or storage mutation when its digest differs."""
    supplied = request.headers.get("Content-Digest")
    if supplied is None:
        return None
    try:
        parsed = _parse_digest_field(supplied)
    except ValueError as exc:
        _digest_audit(username, resource, "webdav_content_digest_rejected", [], len(content))
        return Response(str(exc), 400, {"Want-Content-Digest": DIGEST_PREFERENCE})
    algorithms = list(parsed)
    mismatched = any(
        not hmac.compare_digest(factory(content).digest(), parsed[name])
        for name, (factory, _length, _weight) in DIGEST_ALGORITHMS.items()
        if name in parsed
    )
    if mismatched:
        _digest_audit(username, resource, "webdav_content_digest_mismatch", algorithms, len(content))
        return Response("Content-Digest does not match the uploaded content", 422, {"Want-Content-Digest": DIGEST_PREFERENCE})
    _digest_audit(username, resource, "webdav_content_digest_verified", algorithms, len(content))
    return None


def _content_digest_for_range(handle, start: int, end: int) -> bytes:
    digest = hashlib.sha256()
    for chunk in _iter_file_range(handle, start, end):
        digest.update(chunk)
    return digest.digest()


def _parse_byte_ranges(value: str, size: int) -> list[tuple[int, int]]:
    """Parse a bounded RFC 9110 bytes range-set or raise ValueError for 416."""
    unit, separator, ranges_value = value.partition("=")
    if separator != "=" or unit.strip().casefold() != "bytes":
        raise ValueError("unsupported range unit")
    specifications = [item.strip() for item in ranges_value.split(",")]
    if not specifications or any(not item for item in specifications) or len(specifications) > MAX_BYTE_RANGES:
        raise ValueError("invalid or excessive range set")
    ranges: list[tuple[int, int]] = []
    for specification in specifications:
        first, dash, last = specification.partition("-")
        if dash != "-" or (not first and not last):
            raise ValueError("invalid byte range")
        try:
            if not first:
                suffix = int(last)
                if suffix <= 0 or size <= 0:
                    continue
                start, end = max(0, size - suffix), size - 1
            else:
                start = int(first)
                if start < 0:
                    raise ValueError
                if start >= size:
                    continue
                end = size - 1 if not last else min(int(last), size - 1)
                if end < start:
                    raise ValueError
        except (TypeError, ValueError):
            raise ValueError("invalid byte range") from None
        ranges.append((start, end))
    if not ranges:
        raise ValueError("unsatisfiable byte range")
    ordered = sorted(ranges)
    if any(current[0] <= previous[1] for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("overlapping ranges are rejected")
    return ranges


def _iter_file_range(handle, start: int, end: int):
    remaining = end - start + 1
    handle.seek(start)
    while remaining:
        chunk = handle.read(min(DOWNLOAD_CHUNK_SIZE, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk


def _download_response(path: Path, username: str, document: dict, media_type: str) -> Response:
    """Return a conditional, range-capable response from one stable open-file snapshot."""
    try:
        handle = path.open("rb")
    except OSError:
        return Response("not found", 404)
    try:
        stat = os.fstat(handle.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
        handle.seek(0)
        size = stat.st_size
        etag = f'"{digest.hexdigest()}"'
        representation_digest = _digest_value("sha-256", digest.digest())
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        headers = {
            "ETag": etag,
            "Repr-Digest": representation_digest,
            "Last-Modified": last_modified,
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-cache",
        }
        content_language = _content_language(username, path, document)
        if content_language:
            headers["Content-Language"] = content_language

        if_match = request.headers.get("If-Match")
        if if_match is not None and not _etag_list_matches(if_match, etag, weak=False):
            handle.close()
            return Response("", 412, headers)
        if if_match is None:
            unmodified = request.headers.get("If-Unmodified-Since")
            unmodified_at = _http_date_timestamp(unmodified) if unmodified else None
            if unmodified_at is not None and int(stat.st_mtime) > unmodified_at:
                handle.close()
                return Response("", 412, headers)

        if_none_match = request.headers.get("If-None-Match")
        if if_none_match is not None and _etag_list_matches(if_none_match, etag, weak=True):
            handle.close()
            return Response("", 304, headers)
        if if_none_match is None:
            modified = request.headers.get("If-Modified-Since")
            modified_at = _http_date_timestamp(modified) if modified else None
            if modified_at is not None and int(stat.st_mtime) <= modified_at:
                handle.close()
                return Response("", 304, headers)

        range_header = request.headers.get("Range") if request.method == "GET" else None
        if range_header and request.headers.get("If-Range"):
            validator = request.headers["If-Range"].strip()
            if validator.startswith('"') or validator.startswith("W/"):
                range_allowed = not validator.startswith("W/") and validator == etag
            else:
                range_allowed = validator == last_modified
            if not range_allowed:
                range_header = None

        if range_header:
            try:
                ranges = _parse_byte_ranges(range_header, size)
            except ValueError:
                handle.close()
                return Response("", 416, {**headers, "Content-Range": f"bytes */{size}"})
            if len(ranges) == 1:
                start, end = ranges[0]
                response_headers = {
                    **headers,
                    "Content-Digest": _digest_value("sha-256", _content_digest_for_range(handle, start, end)),
                    "Content-Type": media_type,
                    "Content-Length": str(end - start + 1),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                }

                def single_range():
                    try:
                        yield from _iter_file_range(handle, start, end)
                    finally:
                        handle.close()

                return Response(single_range(), 206, response_headers)

            boundary = f"simpleoffice-{digest.hexdigest()[:24]}"
            parts: list[tuple[bytes, int, int]] = []
            total_length = 0
            for start, end in ranges:
                prefix = (
                    f"--{boundary}\r\nContent-Type: {media_type}\r\n"
                    f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n"
                ).encode("ascii")
                parts.append((prefix, start, end))
                total_length += len(prefix) + end - start + 1 + 2
            closing = f"--{boundary}--\r\n".encode("ascii")
            total_length += len(closing)
            content_digest = hashlib.sha256()
            for prefix, start, end in parts:
                content_digest.update(prefix)
                for chunk in _iter_file_range(handle, start, end):
                    content_digest.update(chunk)
                content_digest.update(b"\r\n")
            content_digest.update(closing)

            def multiple_ranges():
                try:
                    for prefix, start, end in parts:
                        yield prefix
                        yield from _iter_file_range(handle, start, end)
                        yield b"\r\n"
                    yield closing
                finally:
                    handle.close()

            return Response(
                multiple_ranges(), 206,
                {
                    **headers,
                    "Content-Digest": _digest_value("sha-256", content_digest.digest()),
                    "Content-Type": f"multipart/byteranges; boundary={boundary}",
                    "Content-Length": str(total_length),
                },
            )

        if request.method == "HEAD":
            handle.close()
            response = Response(None, 200)
            response.headers.update({**headers, "Content-Type": media_type, "Content-Length": str(size)})
            return response

        def complete_file():
            try:
                yield from _iter_file_range(handle, 0, size - 1)
            finally:
                handle.close()

        return Response(
            complete_file(), 200,
            {
                **headers,
                "Content-Digest": representation_digest,
                "Content-Type": media_type,
                "Content-Length": str(size),
            },
        )
    except Exception:
        handle.close()
        raise


def _resource_url(username: str, document: dict, *, external: bool = False) -> str:
    filename = Path(str(document.get("last_path", "document"))).name
    return url_for("webdav.endpoint", path=f"documents/{username}/{document['document_id']}--{filename}", _external=external)


def _tree_url(username: str, relative: str = "", *, external: bool = False, collection: bool = False) -> str:
    encoded = "/".join(quote(part, safe="") for part in Path(relative).parts if part not in {"", "."})
    suffix = f"/{encoded}" if encoded else ""
    base = request.url_root.rstrip("/") if external else ""
    value = f"{base}/webdav/files/{quote(username, safe='')}{suffix}"
    return value + "/" if collection and not value.endswith("/") else value


def _tree_path(relative_path: str) -> Path:
    store = _store()
    if not relative_path.strip("/"):
        return store.root
    relative = store._safe_managed_relative_path(unquote(relative_path), require_name=True)
    candidate = store.root / relative
    if candidate.is_symlink():
        raise ValueError("symbolic links are not available over WebDAV")
    return candidate


def _portable_name_key(name: str) -> str:
    """Return a stable, conservative comparison key for desktop file systems."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def _portable_name_reason(name: str) -> str:
    """Explain why a new WebDAV member would not round-trip across target clients."""
    if not name or name in {".", ".."}:
        return "empty-or-relative-segment"
    if name != unicodedata.normalize("NFC", name):
        return "unicode-nfc-required"
    if name.startswith(" ") or name.endswith((" ", ".")):
        return "leading-or-trailing-space-or-dot"
    if any(character in WINDOWS_FORBIDDEN_NAME_CHARACTERS for character in name):
        return "windows-reserved-character"
    if any(character in BIDI_CONTROL_CHARACTERS for character in name):
        return "bidirectional-control-character"
    if any(unicodedata.category(character) in {"Cc", "Cs", "Co", "Cn"} for character in name):
        return "non-interchange-character"
    basename = name.rstrip(" .").split(".", 1)[0].upper()
    if basename in WINDOWS_RESERVED_BASENAMES:
        return "windows-reserved-device-name"
    try:
        encoded_length = len(name.encode("utf-8"))
    except UnicodeEncodeError:
        return "invalid-unicode"
    if encoded_length > MAX_PORTABLE_NAME_BYTES:
        return "name-too-long"
    return ""


def _portable_name_error(
    username: str,
    resource: Path,
    *,
    exclude: Path | None = None,
) -> Response | None:
    """Reject ambiguous new names while leaving existing legacy resources operable."""
    siblings: list[Path] = []
    if resource.parent.is_dir() and not resource.parent.is_symlink():
        siblings = list(resource.parent.iterdir())
        if any(sibling != exclude and sibling.name == resource.name for sibling in siblings):
            return None
    elif resource.exists():
        return None
    reason = _portable_name_reason(resource.name)
    if not reason and siblings:
        requested_key = _portable_name_key(resource.name)
        for sibling in siblings:
            if sibling == exclude or sibling.name in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE}:
                continue
            if _portable_name_key(sibling.name) == requested_key:
                reason = "case-or-normalization-collision"
                break
    if not reason:
        return None

    parent = _store().relative(resource.parent)
    name_digest = hashlib.sha256(resource.name.encode("utf-8", errors="surrogatepass")).hexdigest()
    actor = f"webdav:{username}"
    _store().history.record(
        "webdav_portable_name_rejected",
        actor,
        "webdav-name-policy",
        hashlib.sha256(f"{username}:{parent}:{name_digest}".encode()).hexdigest(),
        {
            "actor": actor,
            "method": request.method,
            "parent": parent,
            "name_sha256": name_digest,
            "name_utf8_bytes": len(resource.name.encode("utf-8", errors="surrogatepass")),
            "reason": reason,
            "rejected_at": utc_now(),
        },
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:error xmlns:d="DAV:" xmlns:s="urn:simpleoffice:webdav">'
        f'<s:portable-file-name reason="{reason}"/>'
        '</d:error>'
    )
    return Response(
        xml,
        409,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "no-store",
            "X-SimpleOffice-Name-Reason": reason,
        },
    )


def _tree_document(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("document unavailable")
    return _store().get_document(path)


def _lock_key(path: Path, document: dict | None = None) -> str:
    if document:
        return str(document["document_id"])
    relative = _store().relative(path)
    return "unmapped:" + hashlib.sha256(relative.encode("utf-8")).hexdigest()


def _destination(username: str, identity: dict) -> tuple[Path, str]:
    value = request.headers.get("Destination", "")
    if not value:
        raise ValueError("Destination header is required")
    parsed = urlsplit(value)
    if parsed.netloc and parsed.netloc.casefold() != request.host.casefold():
        raise PermissionError("cross-server destinations are not allowed")
    prefix = f"/webdav/files/{username}/"
    path = unquote(parsed.path)
    if not path.startswith(prefix):
        raise PermissionError("destination must remain in the authenticated user's WebDAV tree")
    relative = path[len(prefix):].strip("/")
    if not relative:
        raise ValueError("the WebDAV root cannot be replaced")
    destination = _tree_path(relative)
    if not _credential_allows_path(identity, destination):
        raise PermissionError("destination is outside the credential's collection")
    return destination, _store().relative(destination)


def _lock_for(key: str) -> dict | None:
    return _active_locks().get("locks", {}).get(key)


def _relative_is_within(candidate: str, collection: str) -> bool:
    candidate_path = Path(candidate or ".")
    collection_path = Path(collection or ".")
    return candidate_path == collection_path or collection_path in candidate_path.parents


def _lock_applies(stored_key: str, lock: dict, resource: Path, document: dict | None) -> bool:
    key = _lock_key(resource, document)
    if stored_key == key:
        return True
    root = str(lock.get("resource", "")).strip()
    if not root or str(lock.get("depth", "0")) != "infinity":
        return False
    relative = _store().relative(resource)
    return relative != root and _relative_is_within(relative, root)


def _locks_for(resource: Path, document: dict | None = None, locks: dict | None = None) -> list[tuple[str, dict]]:
    active = locks if locks is not None else _active_locks().get("locks", {})
    return [
        (stored_key, lock) for stored_key, lock in active.items()
        if _lock_applies(stored_key, lock, resource, document)
    ]


def _conflicting_locks(resource: Path, document: dict | None, depth: str) -> list[tuple[str, dict]]:
    active = _active_locks().get("locks", {})
    conflicts = _locks_for(resource, document, active)
    if depth == "infinity" and resource.is_dir() and not resource.is_symlink():
        relative = _store().relative(resource)
        known = {stored_key for stored_key, _lock in conflicts}
        for stored_key, lock in active.items():
            lock_resource = str(lock.get("resource", "")).strip()
            if stored_key not in known and lock_resource and lock_resource != relative and _relative_is_within(lock_resource, relative):
                conflicts.append((stored_key, lock))
    return conflicts


def _require_lock(resource: Path, document: dict | None, username: str) -> Response | None:
    locks = _locks_for(resource, document)
    token = _request_token(_lock_key(resource, document))
    for _stored_key, lock in locks:
        if lock.get("token") != token or lock.get("username") != username:
            return Response("resource is locked", 423)
    return None


def _release_lock(key: str) -> None:
    path = _locks_path()
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        payload.get("locks", {}).pop(key, None)
        atomic_json_write(path, payload)


def _active_locks() -> dict:
    if request.method == "PROPFIND" and hasattr(g, "_webdav_propfind_locks"):
        return g._webdav_propfind_locks
    now = datetime.now(timezone.utc)
    payload = _read_json(_locks_path(), {"locks": {}})
    locks = payload.setdefault("locks", {})
    locks = {key: value for key, value in locks.items() if datetime.fromisoformat(value["expires_at"]).astimezone(timezone.utc) > now}
    payload["locks"] = locks
    if request.method == "PROPFIND":
        g._webdav_propfind_locks = payload
    return payload


def _parse_if_header(value: str) -> list[tuple[str | None, list[list[tuple[bool, str, str]]]]]:
    """Parse the bounded RFC 4918 If grammar without accepting loose substrings."""
    if len(value.encode("utf-8")) > MAX_IF_HEADER_BYTES:
        raise OverflowError("WebDAV If header is too large")
    position = 0
    list_count = 0
    condition_count = 0

    def whitespace() -> None:
        nonlocal position
        while position < len(value) and value[position] in " \t":
            position += 1

    def enclosed(start: str, end: str) -> str:
        nonlocal position
        if position >= len(value) or value[position] != start:
            raise ValueError("invalid WebDAV If header")
        closing = value.find(end, position + 1)
        if closing < 0:
            raise ValueError("invalid WebDAV If header")
        result = value[position + 1:closing]
        if not result or "\r" in result or "\n" in result or start in result:
            raise ValueError("invalid WebDAV If header")
        position = closing + 1
        return result

    def condition_list() -> list[tuple[bool, str, str]]:
        nonlocal position, list_count, condition_count
        if list_count >= MAX_IF_LISTS:
            raise OverflowError("WebDAV If header contains too many lists")
        list_count += 1
        position += 1
        conditions: list[tuple[bool, str, str]] = []
        while True:
            whitespace()
            if position >= len(value):
                raise ValueError("invalid WebDAV If header")
            if value[position] == ")":
                position += 1
                if not conditions:
                    raise ValueError("WebDAV If lists must not be empty")
                return conditions
            negated = False
            if value[position:position + 3].casefold() == "not":
                following = position + 3
                if following >= len(value) or value[following] not in " \t":
                    raise ValueError("invalid Not condition in WebDAV If header")
                negated = True
                position = following
                whitespace()
            if condition_count >= MAX_IF_CONDITIONS:
                raise OverflowError("WebDAV If header contains too many conditions")
            condition_count += 1
            if value[position] == "<":
                conditions.append((negated, "token", enclosed("<", ">")))
            elif value[position] == "[":
                conditions.append((negated, "etag", enclosed("[", "]")))
            else:
                raise ValueError("invalid condition in WebDAV If header")

    whitespace()
    if not value or position == len(value):
        return []
    groups: list[tuple[str | None, list[list[tuple[bool, str, str]]]]] = []
    if value[position] == "(":
        lists = []
        while True:
            whitespace()
            if position >= len(value):
                break
            if value[position] != "(":
                raise ValueError("tagged and untagged WebDAV If lists cannot be mixed")
            lists.append(condition_list())
        groups.append((None, lists))
        return groups
    while position < len(value):
        whitespace()
        if position >= len(value):
            break
        tag = enclosed("<", ">")
        whitespace()
        lists = []
        while position < len(value) and value[position] == "(":
            lists.append(condition_list())
            whitespace()
        if not lists:
            raise ValueError("tagged WebDAV If resource requires a condition list")
        groups.append((tag, lists))
    return groups


def _if_resource(tag: str | None, username: str, identity: dict) -> dict:
    """Resolve a tagged URI only inside the authenticated WebDAV namespace."""
    parsed = urlsplit(tag or request.path)
    if parsed.netloc and parsed.netloc.casefold() != request.host.casefold():
        raise PermissionError("WebDAV If resource belongs to another server")
    if parsed.query or parsed.fragment:
        raise ValueError("WebDAV If resource must not contain a query or fragment")
    path = unquote(parsed.path)
    tree_prefix = f"/webdav/files/{username}"
    stable_prefix = f"/webdav/documents/{username}/"
    if path.rstrip("/") == tree_prefix:
        relative = ""
        resource = _tree_path(relative)
    elif path.startswith(tree_prefix + "/"):
        relative = path[len(tree_prefix):].strip("/")
        resource = _tree_path(relative)
    elif path.startswith(stable_prefix):
        leaf = path[len(stable_prefix):]
        if "/" in leaf or "--" not in leaf:
            raise ValueError("invalid stable WebDAV resource")
        document_id, requested_name = leaf.split("--", 1)
        document = _store().get_document(document_id)
        resource = _document_path(document)
        if requested_name != resource.name:
            raise ValueError("invalid stable WebDAV resource")
    else:
        raise PermissionError("WebDAV If resource is outside this user tree")
    if not _credential_allows_path(identity, resource):
        raise PermissionError("WebDAV If resource is outside this credential")
    document = None
    if resource.is_file() and not resource.is_symlink():
        document = _tree_document(resource)
    collection = resource.is_dir() and not resource.is_symlink()
    return {
        "resource": resource,
        "document": document,
        "collection": collection,
        "key": _lock_key(resource, document),
        "etag": _etag(document) if document else "",
    }


def _if_condition_matches(condition: tuple[bool, str, str], state: dict, username: str, locks: dict) -> bool:
    negated, kind, supplied = condition
    matched = False
    if kind == "etag":
        matched = bool(state["etag"]) and not supplied.startswith("W/") and hmac.compare_digest(supplied, state["etag"])
    elif supplied.casefold().startswith("opaquelocktoken:"):
        matched = any(
            lock.get("username") == username and hmac.compare_digest(str(lock.get("token", "")), supplied)
            for _stored_key, lock in _locks_for(state["resource"], state["document"], locks)
        )
    elif supplied.casefold().startswith("urn:uuid:") and state["collection"]:
        if "sync_token" not in state:
            state["sync_token"] = _collection_sync_token(username, state["resource"])
        current = state["sync_token"]
        matched = hmac.compare_digest(current, supplied)
    return not matched if negated else matched


def _if_header_error(username: str, identity: dict) -> Response | None:
    """Evaluate RFC 4918 If lists and cache matching lock tokens by resource."""
    if getattr(g, "_webdav_if_checked", False):
        return None
    value = request.headers.get("If", "")
    g._webdav_if_checked = True
    g._webdav_if_tokens = {}
    g._webdav_if_etags = {}
    if not value:
        return None
    try:
        groups = _parse_if_header(value)
        locks = _active_locks().get("locks", {})
        for tag, lists in groups:
            state = _if_resource(tag, username, identity)
            successful = [
                conditions for conditions in lists
                if all(_if_condition_matches(condition, state, username, locks) for condition in conditions)
            ]
            if not successful:
                return Response("WebDAV If precondition failed", 412)
            tokens = {
                supplied for conditions in successful for negated, kind, supplied in conditions
                if not negated and kind == "token" and supplied.casefold().startswith("opaquelocktoken:")
            }
            if tokens:
                g._webdav_if_tokens.setdefault(state["key"], set()).update(tokens)
            etags = {
                supplied for conditions in successful for negated, kind, supplied in conditions
                if not negated and kind == "etag"
            }
            if etags:
                g._webdav_if_etags.setdefault(state["key"], set()).update(etags)
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        return Response("WebDAV If precondition targets an inaccessible resource", 412)
    except ValueError:
        return Response("invalid WebDAV If header or resource", 400)
    return None


def _request_token(key: str) -> str:
    tokens = getattr(g, "_webdav_if_tokens", {}).get(key, set())
    return next(iter(tokens)) if len(tokens) == 1 else ""


def _request_etag(key: str, current_etag: str) -> bool:
    """Return whether a successful DAV If list named this strong ETag."""
    return any(
        not supplied.startswith("W/") and hmac.compare_digest(supplied, current_etag)
        for supplied in getattr(g, "_webdav_if_etags", {}).get(key, set())
    )


def _unlock_token() -> tuple[str, Response | None]:
    value = request.headers.get("Lock-Token", "").strip()
    match = re.fullmatch(r"<(opaquelocktoken:[0-9a-fA-F-]+)>", value)
    if not match:
        return "", Response("UNLOCK requires exactly one valid Lock-Token header", 400)
    return match.group(1), None


def _save_lock(
    document_id: str,
    username: str,
    token: str,
    timeout_seconds: int,
    owner: str = "",
    *,
    href: str = "",
    depth: str = "0",
    resource: str = "",
) -> dict:
    path = _locks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        existing = payload["locks"].get(document_id)
        if existing and existing.get("token") != token:
            raise PermissionError("document is already locked")
        lock = {
            "token": token,
            "username": username,
            "owner": owner[:200],
            "href": href or str(existing.get("href", "") if existing else ""),
            "depth": depth,
            "resource": resource or str(existing.get("resource", "") if existing else ""),
            "created_at": existing.get("created_at", utc_now()) if existing else utc_now(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat(),
        }
        payload["locks"][document_id] = lock
        atomic_json_write(path, payload)
        return lock


def _timeout_seconds() -> int:
    match = re.search(r"Second-(\d+)", request.headers.get("Timeout", ""), re.I)
    return max(60, min(int(match.group(1)) if match else 1800, 3600))


def _activelock_xml(lock: dict, href: str) -> str:
    seconds = max(0, int((datetime.fromisoformat(lock["expires_at"]) - datetime.now(timezone.utc)).total_seconds()))
    return f'''<d:activelock><d:locktype><d:write/></d:locktype><d:lockscope><d:exclusive/></d:lockscope><d:depth>{escape(str(lock.get("depth", "0")))}</d:depth><d:owner>{escape(lock.get("owner", ""))}</d:owner><d:timeout>Second-{seconds}</d:timeout><d:locktoken><d:href>{escape(lock["token"])}</d:href></d:locktoken><d:lockroot><d:href>{escape(str(lock.get("href", href)))}</d:href></d:lockroot></d:activelock>'''


def _lockdiscovery_xml(lock: dict | None, href: str) -> str:
    active = _activelock_xml(lock, href) if lock else ""
    return f'<d:lockdiscovery xmlns:d="DAV:">{active}</d:lockdiscovery>'


def _lock_xml(lock: dict, href: str) -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?><d:prop xmlns:d="DAV:">{_lockdiscovery_xml(lock, href)}</d:prop>'''


def _record_lock_audit(action: str, username: str, resource: Path, lock: dict) -> None:
    relative = _store().relative(resource)
    _store().history.record(
        action,
        f"webdav:{username}",
        "webdav-locks",
        hashlib.sha256(f"{username}:{relative}".encode()).hexdigest(),
        {
            "resource": relative,
            "depth": str(lock.get("depth", "0")),
            "owner_present": bool(lock.get("owner")),
            "expires_at": str(lock.get("expires_at", "")),
            "changed_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _parse_lock_body(body: bytes) -> str:
    root = _safe_xml_root(body, f"{{{DAV}}}lockinfo")
    allowed = {f"{{{DAV}}}lockscope", f"{{{DAV}}}locktype", f"{{{DAV}}}owner"}
    if any(child.tag not in allowed for child in root):
        raise ValueError("LOCK body contains an unsupported element")
    scopes = root.findall(f"{{{DAV}}}lockscope")
    types = root.findall(f"{{{DAV}}}locktype")
    owners = root.findall(f"{{{DAV}}}owner")
    if len(scopes) != 1 or len(types) != 1 or len(owners) > 1:
        raise ValueError("LOCK requires one lockscope and one locktype")
    if [child.tag for child in scopes[0]] != [f"{{{DAV}}}exclusive"]:
        raise ValueError("only exclusive WebDAV locks are supported")
    if [child.tag for child in types[0]] != [f"{{{DAV}}}write"]:
        raise ValueError("only write locks are supported")
    owner = "".join(owners[0].itertext()).strip() if owners else ""
    if len(owner.encode("utf-8")) > 1024:
        raise OverflowError("LOCK owner is too large")
    return owner


def _lock_request(username: str, resource: Path, document: dict | None, href: str) -> Response:
    """Create or explicitly refresh an RFC 4918 exclusive write lock."""
    body = request.get_data(cache=True)
    key = _lock_key(resource, document)
    existing = _lock_for(key)

    if not body.strip():
        token = _request_token(key)
        if not token or not existing or existing.get("token") != token or existing.get("username") != username:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:lock-token-matches-request-uri/></d:error>'
            return Response(error, 412, mimetype="application/xml")
        lock = _save_lock(
            key, username, token, _timeout_seconds(), existing.get("owner", ""),
            href=str(existing.get("href", href)), depth=str(existing.get("depth", "0")),
            resource=_store().relative(resource),
        )
        _record_lock_audit("webdav_lock_refreshed", username, resource, lock)
        return Response(_lock_xml(lock, href), 200, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})

    depth = request.headers.get("Depth", "infinity").casefold()
    if depth not in {"0", "infinity"}:
        return Response("LOCK Depth must be 0 or infinity", 400)

    try:
        owner = _parse_lock_body(body)
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml")
    except ValueError as exc:
        return Response(str(exc), 400)
    effective_depth = depth if resource.is_dir() else "0"
    if _conflicting_locks(resource, document, effective_depth):
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-conflicting-lock/></d:error>'
        return Response(error, 423, mimetype="application/xml")
    if document is not None:
        try:
            _store()._require_document_editable(document)
        except ValueError as exc:
            return Response(str(exc), 423)

    token = f"opaquelocktoken:{uuid.uuid4()}"
    status = 200
    if not resource.exists():
        if not resource.parent.is_dir() or resource.parent.is_symlink():
            return Response("parent collection does not exist", 409)
        provisional_key = key
        try:
            _save_lock(
                provisional_key, username, token, _timeout_seconds(), owner,
                href=href, depth="0", resource=_store().relative(resource),
            )
            document = _store().create_document_at(
                _store().relative(resource), b"", f"webdav:{username}",
                max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
            )
        except (FileExistsError, ValueError) as exc:
            _release_lock(provisional_key)
            return Response(str(exc), 409)
        _release_lock(provisional_key)
        key = _lock_key(resource, document)
        _record_sync_changes(username, _store().relative(resource))
        status = 201
    try:
        lock = _save_lock(
            key, username, token, _timeout_seconds(), owner,
            href=href, depth=effective_depth, resource=_store().relative(resource),
        )
    except PermissionError:
        return Response("locked", 423)
    _record_lock_audit("webdav_lock_created", username, resource, lock)
    return Response(
        _lock_xml(lock, href), status,
        {"Content-Type": "application/xml; charset=utf-8", "Lock-Token": f"<{token}>", "Cache-Control": "no-store"},
    )


def _safe_xml_root(body: bytes, expected_tag: str) -> ElementTree.Element:
    """Parse bounded WebDAV XML without accepting entity declarations."""
    if len(body) > MAX_PROPERTY_BODY:
        raise OverflowError("WebDAV XML body is too large")
    upper = body.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PermissionError("external and declared XML entities are not allowed")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid WebDAV XML") from exc
    if root.tag != expected_tag:
        raise ValueError("unexpected WebDAV XML root")
    if sum(1 for _ in root.iter()) > MAX_PROPERTY_NODES:
        raise OverflowError("WebDAV XML contains too many elements")
    return root


def _property_resource_key(username: str, resource: Path, document: dict | None) -> str:
    if document is not None:
        stable = f"document:{document['document_id']}"
    else:
        policy = _read_json(resource / POLICY_FILE, {})
        folder_id = str(policy.get("folder_id", "")).strip()
        stable = f"collection:{folder_id}" if folder_id else f"collection-path:{_store().relative(resource)}"
    return f"{username}:{stable}"


def _dead_properties(username: str, resource: Path, document: dict | None) -> dict[str, str]:
    key = _property_resource_key(username, resource, document)
    if request.method in {"PROPFIND", "SEARCH"}:
        if not hasattr(g, "_webdav_propfind_properties"):
            g._webdav_propfind_properties = _read_json(
                _properties_path(), {"resources": {}},
            ).get("resources", {})
        resources = g._webdav_propfind_properties
    else:
        resources = _read_json(_properties_path(), {"resources": {}}).get("resources", {})
    properties = resources.get(key, {}) if isinstance(resources, dict) else {}
    if not isinstance(properties, dict):
        return {}
    return {
        str(name): str(value) for name, value in properties.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def _content_language(username: str, resource: Path, document: dict | None) -> str:
    serialized = _dead_properties(username, resource, document).get(f"{{{DAV}}}getcontentlanguage", "")
    if not serialized:
        return ""
    try:
        return (ElementTree.fromstring(serialized).text or "").strip()
    except ElementTree.ParseError:
        return ""


def _xml_element(tag: str, text: str | None = None, child: ElementTree.Element | None = None) -> str:
    element = ElementTree.Element(tag)
    if text is not None:
        element.text = text
    if child is not None:
        element.append(child)
    return ElementTree.tostring(element, encoding="unicode", short_empty_elements=True)


def _rfc3339_timestamp(value: object) -> str:
    """Return a canonical UTC RFC 3339 value or leave unreliable legacy data undefined."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resource_creationdate(
    resource: Path,
    document: dict | None,
    *,
    collection: bool,
) -> str:
    if collection:
        policy = _read_json(resource / POLICY_FILE, {})
        return _rfc3339_timestamp(policy.get("created_at"))
    return _rfc3339_timestamp((document or {}).get("first_seen_at"))


def _principal_url(username: str, *, collection: bool = False) -> str:
    return url_for(
        "webdav.principal_resource",
        username=username,
        principal_id="" if collection else "self",
    )


def _href_property(tag: str, href: str) -> str:
    element = ElementTree.Element(tag)
    ElementTree.SubElement(element, f"{{{DAV}}}href").text = href
    return ElementTree.tostring(element, encoding="unicode")


def _access_control_live_properties(*, collection: bool) -> dict[str, str]:
    """Expose the current credential's effective, read-only privilege view."""
    identity = getattr(g, "_webdav_identity", None)
    if not isinstance(identity, dict) or not identity.get("username"):
        return {}
    principal = _principal_url(identity["username"])
    principal_collection = _principal_url(identity["username"], collection=True)
    privileges = ["read", "read-current-user-privilege-set"]
    if identity.get("scope") == "write":
        privileges.extend(["write", "write-properties", "write-content", "unlock"])
        if collection:
            privileges.extend(["bind", "unbind"])
    privilege_set = ElementTree.Element(f"{{{DAV}}}current-user-privilege-set")
    for name in privileges:
        privilege = ElementTree.SubElement(privilege_set, f"{{{DAV}}}privilege")
        ElementTree.SubElement(privilege, f"{{{DAV}}}{name}")
    return {
        f"{{{DAV}}}owner": _href_property(f"{{{DAV}}}owner", principal),
        f"{{{DAV}}}current-user-principal": _href_property(
            f"{{{DAV}}}current-user-principal", principal,
        ),
        f"{{{DAV}}}principal-collection-set": _href_property(
            f"{{{DAV}}}principal-collection-set", principal_collection,
        ),
        f"{{{DAV}}}current-user-privilege-set": ElementTree.tostring(
            privilege_set, encoding="unicode",
        ),
    }


def _search_discovery_live_properties(*, collection: bool) -> dict[str, str]:
    """Advertise RFC 5323 only on hierarchical resources that execute SEARCH."""
    identity = getattr(g, "_webdav_identity", None)
    if not isinstance(identity, dict):
        return {}
    methods = ["OPTIONS", "PROPFIND", "SEARCH", "GET", "HEAD"]
    if collection:
        methods.insert(2, "REPORT")
    if identity.get("scope") == "write":
        methods[2:2] = ["PROPPATCH"]
        methods.extend(["PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
    supported_methods = ElementTree.Element(f"{{{DAV}}}supported-method-set")
    for name in methods:
        ElementTree.SubElement(
            supported_methods, f"{{{DAV}}}supported-method", {"name": name},
        )
    grammars = ElementTree.Element(f"{{{DAV}}}supported-query-grammar-set")
    supported = ElementTree.SubElement(
        grammars, f"{{{DAV}}}supported-query-grammar",
    )
    grammar = ElementTree.SubElement(supported, f"{{{DAV}}}grammar")
    ElementTree.SubElement(grammar, f"{{{DAV}}}basicsearch")
    return {
        f"{{{DAV}}}supported-method-set": ElementTree.tostring(
            supported_methods, encoding="unicode",
        ),
        f"{{{DAV}}}supported-query-grammar-set": ElementTree.tostring(
            grammars, encoding="unicode",
        ),
    }


def _live_properties(
    display_name: str,
    *,
    collection: bool,
    document: dict | None,
    sync_token: str = "",
    quota: dict[str, int] | None = None,
    lock: dict | None = None,
    href: str = "",
    resource: Path | None = None,
    searchable: bool = False,
) -> dict[str, str]:
    supported = ElementTree.Element(f"{{{DAV}}}supportedlock")
    entry = ElementTree.SubElement(supported, f"{{{DAV}}}lockentry")
    scope = ElementTree.SubElement(entry, f"{{{DAV}}}lockscope")
    ElementTree.SubElement(scope, f"{{{DAV}}}exclusive")
    locktype = ElementTree.SubElement(entry, f"{{{DAV}}}locktype")
    ElementTree.SubElement(locktype, f"{{{DAV}}}write")
    values = {
        f"{{{DAV}}}displayname": _xml_element(f"{{{DAV}}}displayname", display_name),
        f"{{{DAV}}}supportedlock": ElementTree.tostring(supported, encoding="unicode"),
        f"{{{DAV}}}lockdiscovery": _lockdiscovery_xml(lock, href),
        f"{{{DAV}}}iscollection": _xml_element(
            f"{{{DAV}}}iscollection", "1" if collection else "0",
        ),
        f"{{{DAV}}}isFolder": _xml_element(
            f"{{{DAV}}}isFolder", "t" if collection else "f",
        ),
        f"{{{DAV}}}ishidden": _xml_element(
            f"{{{DAV}}}ishidden",
            "1" if resource is not None and resource.name.startswith(".") else "0",
        ),
        **_access_control_live_properties(collection=collection),
        **(_search_discovery_live_properties(collection=collection) if searchable else {}),
    }
    path = resource or (_document_path(document) if document else None)
    if path is not None:
        created_at = _resource_creationdate(path, document, collection=collection)
        if created_at:
            values[f"{{{DAV}}}creationdate"] = _xml_element(
                f"{{{DAV}}}creationdate", created_at,
            )
        stat = path.stat()
        values[f"{{{DAV}}}getlastmodified"] = _xml_element(
            f"{{{DAV}}}getlastmodified", formatdate(stat.st_mtime, usegmt=True),
        )
    if collection:
        resource_type = ElementTree.Element(f"{{{DAV}}}resourcetype")
        ElementTree.SubElement(resource_type, f"{{{DAV}}}collection")
        report_set = ElementTree.Element(f"{{{DAV}}}supported-report-set")
        supported_report = ElementTree.SubElement(report_set, f"{{{DAV}}}supported-report")
        report = ElementTree.SubElement(supported_report, f"{{{DAV}}}report")
        ElementTree.SubElement(report, f"{{{DAV}}}sync-collection")
        values.update({
            f"{{{DAV}}}resourcetype": ElementTree.tostring(resource_type, encoding="unicode"),
            f"{{{DAV}}}supported-report-set": ElementTree.tostring(report_set, encoding="unicode"),
        })
        if sync_token:
            values[f"{{{DAV}}}sync-token"] = _xml_element(f"{{{DAV}}}sync-token", sync_token)
        if quota is not None:
            values[f"{{{DAV}}}quota-available-bytes"] = _xml_element(
                f"{{{DAV}}}quota-available-bytes", str(quota["available"])
            )
            values[f"{{{DAV}}}quota-used-bytes"] = _xml_element(
                f"{{{DAV}}}quota-used-bytes", str(quota["used"])
            )
        return values
    if path is None:
        return values
    values.update({
        f"{{{DAV}}}resourcetype": _xml_element(f"{{{DAV}}}resourcetype"),
        f"{{{DAV}}}getcontentlength": _xml_element(f"{{{DAV}}}getcontentlength", str(stat.st_size)),
        f"{{{DAV}}}getcontenttype": _xml_element(f"{{{DAV}}}getcontenttype", mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
        f"{{{DAV}}}getetag": _xml_element(f"{{{DAV}}}getetag", _etag(document or {})),
    })
    return values


def _parse_propfind(body: bytes) -> tuple[str, list[str]]:
    if not body.strip():
        return "allprop", []
    root = _safe_xml_root(body, f"{{{DAV}}}propfind")
    selectors = [child for child in root if child.tag in {f"{{{DAV}}}allprop", f"{{{DAV}}}propname", f"{{{DAV}}}prop"}]
    if len(selectors) != 1:
        raise ValueError("PROPFIND requires exactly one property selector")
    selector = selectors[0]
    include_nodes = root.findall(f"{{{DAV}}}include")
    allowed = {selector, *include_nodes}
    if len(include_nodes) > 1 or any(child not in allowed for child in root):
        raise ValueError("PROPFIND contains an unsupported instruction")
    if selector.tag != f"{{{DAV}}}allprop" and include_nodes:
        raise ValueError("DAV:include is only valid with DAV:allprop")
    if selector.tag == f"{{{DAV}}}prop":
        if any(child.attrib or list(child) or (child.text or "").strip() for child in selector):
            raise ValueError("PROPFIND property selectors must not contain values")
        requested = [child.tag for child in selector]
        return "prop", requested
    if selector.tag == f"{{{DAV}}}propname":
        if selector.attrib or list(selector) or (selector.text or "").strip():
            raise ValueError("DAV:propname must be empty")
        return "propname", []
    if selector.attrib or list(selector) or (selector.text or "").strip():
        raise ValueError("DAV:allprop must be empty")
    include = include_nodes[0] if include_nodes else None
    if include is not None and any(child.attrib or list(child) or (child.text or "").strip() for child in include):
        raise ValueError("DAV:include property selectors must not contain values")
    return "allprop", [child.tag for child in include] if include is not None else []


def _empty_property(tag: str) -> str:
    return ElementTree.tostring(ElementTree.Element(tag), encoding="unicode", short_empty_elements=True)


def _prop_response(
    href: str,
    display_name: str,
    *,
    collection: bool = False,
    document: dict | None = None,
    sync_token: str = "",
    username: str = "",
    resource: Path | None = None,
    query: tuple[str, list[str]] | None = None,
    searchable: bool = False,
) -> str:
    applicable = _locks_for(resource, document) if resource is not None else []
    active_lock = applicable[0][1] if applicable else None
    live = _live_properties(
        display_name,
        collection=collection,
        document=document,
        sync_token=sync_token,
        quota=_quota_state() if collection and username else None,
        lock=active_lock,
        href=href,
        resource=resource,
        searchable=searchable,
    )
    dead = _dead_properties(username, resource, document) if username and resource is not None else {}
    propstats = _property_propstats(live, dead, query)
    return f'<d:response><d:href>{escape(href)}</d:href>{propstats}</d:response>'


def _property_propstats(
    live: dict[str, str],
    dead: dict[str, str],
    query: tuple[str, list[str]] | None,
) -> str:
    mode, requested = query or ("allprop", [])
    available = {**live, **dead}
    if mode == "propname":
        successful = [_empty_property(tag) for tag in available]
        missing: list[str] = []
    elif mode == "prop":
        successful = [available[tag] for tag in requested if tag in available]
        missing = [_empty_property(tag) for tag in requested if tag not in available]
    else:
        # RFC 4918 allprop includes dead properties and the live properties in
        # that RFC. Extension properties such as sync-token require include.
        extensions = {
            f"{{{DAV}}}alternate-URI-set", f"{{{DAV}}}current-user-principal",
            f"{{{DAV}}}current-user-privilege-set", f"{{{DAV}}}group-membership",
            f"{{{DAV}}}owner", f"{{{DAV}}}principal-collection-set",
            f"{{{DAV}}}principal-URL",
            f"{{{DAV}}}quota-available-bytes", f"{{{DAV}}}quota-used-bytes",
            f"{{{DAV}}}sync-token", f"{{{DAV}}}supported-method-set",
            f"{{{DAV}}}supported-query-grammar-set", f"{{{DAV}}}supported-report-set",
        }
        selected_live = {tag: value for tag, value in live.items() if tag not in extensions}
        available = {**selected_live, **dead}
        for tag in requested:
            if tag in live:
                available[tag] = live[tag]
        successful = list(available.values())
        missing = [_empty_property(tag) for tag in requested if tag not in available]
    propstats = f'<d:propstat><d:prop>{"".join(successful)}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>'
    if missing:
        propstats += f'<d:propstat><d:prop>{"".join(missing)}</d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>'
    return propstats


def _principal_prop_response(
    href: str,
    username: str,
    *,
    collection: bool,
    query: tuple[str, list[str]],
) -> str:
    resource_type = ElementTree.Element(f"{{{DAV}}}resourcetype")
    ElementTree.SubElement(
        resource_type, f"{{{DAV}}}{'collection' if collection else 'principal'}",
    )
    live = {
        f"{{{DAV}}}displayname": _xml_element(
            f"{{{DAV}}}displayname",
            "SimpleOffice Principals" if collection else username,
        ),
        f"{{{DAV}}}resourcetype": ElementTree.tostring(resource_type, encoding="unicode"),
        **_access_control_live_properties(collection=collection),
    }
    if not collection:
        live.update({
            f"{{{DAV}}}alternate-URI-set": _xml_element(f"{{{DAV}}}alternate-URI-set"),
            f"{{{DAV}}}principal-URL": _href_property(
                f"{{{DAV}}}principal-URL", _principal_url(username),
            ),
            f"{{{DAV}}}group-membership": _xml_element(f"{{{DAV}}}group-membership"),
        })
    propstats = _property_propstats(live, {}, query)
    return f'<d:response><d:href>{escape(href)}</d:href>{propstats}</d:response>'


def _propfind_members(resource: Path, depth: str) -> list[tuple[Path, bool, dict | None]]:
    """Return a deterministic, bounded snapshot of DAV-compliant descendants."""
    if depth == "0":
        return []
    members: list[tuple[Path, bool, dict | None]] = []
    pending: list[tuple[Path, int]] = [(resource, 0)]
    visited = 0
    while pending:
        parent, parent_depth = pending.pop()
        try:
            children = sorted(parent.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise _PropfindLimitError("tree-changed", len(members), 0) from exc
        nested_collections: list[tuple[Path, int]] = []
        for child in children:
            if child.name in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE} or child.is_symlink():
                continue
            visited += 1
            if visited > MAX_WEBDAV_COLLECTION_MEMBERS:
                raise _PropfindLimitError(
                    "member-count", visited, MAX_WEBDAV_COLLECTION_MEMBERS,
                )
            child_depth = parent_depth + 1
            if depth == "infinity" and child_depth > MAX_WEBDAV_COLLECTION_DEPTH:
                raise _PropfindLimitError(
                    "nesting-depth", child_depth, MAX_WEBDAV_COLLECTION_DEPTH,
                )
            try:
                if child.is_dir():
                    members.append((child, True, None))
                    if depth == "infinity":
                        nested_collections.append((child, child_depth))
                elif child.is_file():
                    try:
                        document = _tree_document(child)
                    except ValueError:
                        continue
                    members.append((child, False, document))
            except OSError as exc:
                raise _PropfindLimitError("tree-changed", len(members), 0) from exc
        pending.extend(reversed(nested_collections))
    return members


def _propfind_limit_response(
    username: str,
    resource: Path,
    error: _PropfindLimitError,
) -> Response:
    """Return a complete error instead of a truncated or ambiguous tree listing."""
    actor = f"webdav:{username}"
    relative = _store().relative(resource)
    _store().history.record(
        "webdav_propfind_limit_rejected",
        actor,
        "webdav-propfind",
        hashlib.sha256(f"{username}:{relative}:{error.reason}".encode()).hexdigest(),
        {
            "actor": actor,
            "resource": relative,
            "reason": error.reason,
            "observed": error.observed,
            "limit": error.limit,
            "rejected_at": utc_now(),
        },
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:error xmlns:d="DAV:" xmlns:s="urn:simpleoffice:webdav">'
        f'<s:propfind-resource-limit reason="{error.reason}"/>'
        '</d:error>'
    )
    return Response(
        xml,
        507,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "X-SimpleOffice-Propfind-Limit": error.reason,
        },
    )


def _append_propfind_response(
    responses: list[str], response: str, current_size: int,
) -> int:
    """Bound memory while constructing a potentially property-heavy result."""
    size = current_size + len(response.encode("utf-8"))
    if size > MAX_PROPFIND_RESPONSE_BYTES:
        raise _PropfindLimitError("response-bytes", size, MAX_PROPFIND_RESPONSE_BYTES)
    responses.append(response)
    return size


def _propfind_multistatus(responses: list[str]) -> str:
    return PROPFIND_XML_PREFIX + "".join(responses) + PROPFIND_XML_SUFFIX


def _search_error_response(error: _SearchError) -> Response:
    condition = (
        f"<d:{error.condition}/>" if error.condition
        else '<s:invalid-search xmlns:s="urn:simpleoffice:webdav"/>'
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<d:error xmlns:d="DAV:">{condition}</d:error>'
    )
    return Response(
        xml,
        error.status,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
            "X-SimpleOffice-Search-Error": error.message,
        },
    )


def _search_property_tag(node: ElementTree.Element) -> str:
    if node.tag != f"{{{DAV}}}prop" or node.attrib or (node.text or "").strip():
        raise _SearchError(400, "property-operand-invalid")
    properties = list(node)
    if len(properties) != 1:
        raise _SearchError(400, "property-operand-count")
    selected = properties[0]
    if selected.attrib or list(selected) or (selected.text or "").strip():
        raise _SearchError(400, "property-selector-must-be-empty")
    return selected.tag


def _search_caseless(node: ElementTree.Element) -> bool:
    if any(name != "caseless" for name in node.attrib):
        raise _SearchError(400, "unsupported-search-attribute")
    value = node.attrib.get("caseless", "no")
    if value not in {"yes", "no"}:
        raise _SearchError(400, "caseless-must-be-yes-or-no")
    return value == "yes"


@functools.lru_cache(maxsize=128)
def _search_like_regex(pattern: str, caseless: bool) -> str:
    value = unicodedata.normalize("NFC", pattern.casefold() if caseless else pattern)
    parts: list[str] = []
    position = 0
    while position < len(value):
        character = value[position]
        if character == "%":
            parts.append(".*")
        elif character == "_":
            parts.append(".")
        elif character == "\\":
            position += 1
            if position >= len(value) or value[position] not in {"%", "_", "\\"}:
                raise _SearchError(422, "invalid-like-escape")
            parts.append(re.escape(value[position]))
        else:
            parts.append(re.escape(character))
        position += 1
    return "".join(parts)


def _validate_search_operator(
    node: ElementTree.Element, *, depth: int = 1, count: list[int] | None = None,
) -> int:
    if depth > MAX_SEARCH_EXPRESSION_DEPTH:
        raise _SearchError(422, "search-expression-too-deep")
    counter = count if count is not None else [0]
    counter[0] += 1
    if counter[0] > MAX_SEARCH_OPERATORS:
        raise _SearchError(422, "too-many-search-operators")
    logical = {
        f"{{{DAV}}}and": (1, None),
        f"{{{DAV}}}or": (1, None),
        f"{{{DAV}}}not": (1, 1),
    }
    comparisons = {
        f"{{{DAV}}}eq", f"{{{DAV}}}lt", f"{{{DAV}}}lte",
        f"{{{DAV}}}gt", f"{{{DAV}}}gte", f"{{{DAV}}}like",
    }
    if node.tag in logical:
        if node.attrib or (node.text or "").strip():
            raise _SearchError(400, "logical-operator-invalid")
        minimum, maximum = logical[node.tag]
        children = list(node)
        if len(children) < minimum or (maximum is not None and len(children) > maximum):
            raise _SearchError(400, "logical-operand-count")
        for child in children:
            _validate_search_operator(child, depth=depth + 1, count=counter)
        return counter[0]
    if node.tag in comparisons:
        caseless = _search_caseless(node)
        children = list(node)
        if len(children) != 2 or children[1].tag != f"{{{DAV}}}literal":
            raise _SearchError(422, "unsupported-search-operand")
        _search_property_tag(children[0])
        literal = children[1]
        if literal.attrib or list(literal):
            raise _SearchError(422, "unsupported-search-literal")
        value = literal.text or ""
        if len(value.encode("utf-8")) > MAX_PROPERTY_VALUE:
            raise _SearchError(413, "search-literal-too-large")
        if node.tag == f"{{{DAV}}}like":
            _search_like_regex(value, caseless)
        return counter[0]
    if node.tag == f"{{{DAV}}}is-collection":
        if node.attrib or list(node) or (node.text or "").strip():
            raise _SearchError(400, "is-collection-must-be-empty")
        return counter[0]
    if node.tag == f"{{{DAV}}}is-defined":
        if node.attrib or (node.text or "").strip() or len(node) != 1:
            raise _SearchError(400, "is-defined-invalid")
        _search_property_tag(node[0])
        return counter[0]
    raise _SearchError(422, "search-operator-not-supported")


def _parse_search(body: bytes) -> dict:
    try:
        root = _safe_xml_root(body, f"{{{DAV}}}searchrequest")
    except OverflowError:
        raise
    except PermissionError:
        raise
    except ValueError as exc:
        raise _SearchError(400, "invalid-search-xml") from exc
    if root.attrib or (root.text or "").strip() or len(root) != 1:
        raise _SearchError(400, "searchrequest-requires-one-grammar")
    basic = root[0]
    if basic.tag != f"{{{DAV}}}basicsearch":
        raise _SearchError(422, "search-grammar-not-supported", "search-grammar-supported")
    expected = [
        f"{{{DAV}}}select", f"{{{DAV}}}from", f"{{{DAV}}}where",
        f"{{{DAV}}}orderby", f"{{{DAV}}}limit",
    ]
    children = list(basic)
    positions = [expected.index(child.tag) if child.tag in expected else -1 for child in children]
    if (
        basic.attrib or (basic.text or "").strip()
        or len(children) < 2 or positions[:2] != [0, 1]
        or -1 in positions or positions != sorted(set(positions))
    ):
        raise _SearchError(400, "basicsearch-structure-invalid")
    sections = {child.tag: child for child in children}

    select = sections[f"{{{DAV}}}select"]
    if select.attrib or (select.text or "").strip() or len(select) != 1:
        raise _SearchError(400, "select-requires-one-selector")
    selector = select[0]
    if selector.tag == f"{{{DAV}}}allprop":
        if selector.attrib or list(selector) or (selector.text or "").strip():
            raise _SearchError(400, "allprop-must-be-empty")
        query = ("allprop", [])
    elif selector.tag == f"{{{DAV}}}prop":
        if selector.attrib or (selector.text or "").strip():
            raise _SearchError(400, "select-prop-invalid")
        requested = []
        for property_node in selector:
            if property_node.attrib or list(property_node) or (property_node.text or "").strip():
                raise _SearchError(400, "property-selector-must-be-empty")
            requested.append(property_node.tag)
        if not requested or len(requested) > MAX_PROPERTY_COUNT:
            raise _SearchError(413, "selected-property-count-invalid")
        query = ("prop", requested)
    else:
        raise _SearchError(400, "select-grammar-invalid")

    from_node = sections[f"{{{DAV}}}from"]
    scopes = list(from_node)
    if from_node.attrib or (from_node.text or "").strip() or not scopes:
        raise _SearchError(400, "search-scope-required")
    if len(scopes) != 1:
        raise _SearchError(422, "multiple-search-scopes-not-supported", "search-multiple-scope-supported")
    scope = scopes[0]
    if scope.tag != f"{{{DAV}}}scope" or scope.attrib:
        raise _SearchError(400, "search-scope-invalid")
    scope_children = list(scope)
    if [child.tag for child in scope_children] != [f"{{{DAV}}}href", f"{{{DAV}}}depth"]:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid")
    href_node, depth_node = scope_children
    if any(node.attrib or list(node) for node in (href_node, depth_node)):
        raise _SearchError(400, "search-scope-value-invalid")
    scope_href = (href_node.text or "").strip()
    scope_depth = (depth_node.text or "").strip().casefold()
    if not scope_href or len(scope_href.encode("utf-8")) > 2048:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid")
    if scope_depth not in {"0", "1", "infinity"}:
        raise _SearchError(409, "search-depth-invalid", "search-scope-valid")

    where = sections.get(f"{{{DAV}}}where")
    operator = None
    operator_count = 0
    if where is not None:
        if where.attrib or (where.text or "").strip() or len(where) != 1:
            raise _SearchError(400, "where-requires-one-operator")
        operator = where[0]
        operator_count = _validate_search_operator(operator)

    order_by: list[tuple[str, bool, bool]] = []
    orderby = sections.get(f"{{{DAV}}}orderby")
    if orderby is not None:
        orders = list(orderby)
        if orderby.attrib or (orderby.text or "").strip() or not orders:
            raise _SearchError(400, "orderby-invalid")
        if len(orders) > MAX_SEARCH_ORDERS:
            raise _SearchError(422, "too-many-sort-orders")
        for order in orders:
            if order.tag != f"{{{DAV}}}order" or (order.text or "").strip():
                raise _SearchError(400, "order-invalid")
            caseless = _search_caseless(order)
            operands = list(order)
            if len(operands) not in {1, 2}:
                raise _SearchError(400, "order-operand-count")
            property_tag = _search_property_tag(operands[0])
            descending = False
            if len(operands) == 2:
                direction = operands[1]
                if direction.tag not in {f"{{{DAV}}}ascending", f"{{{DAV}}}descending"} or direction.attrib or list(direction) or (direction.text or "").strip():
                    raise _SearchError(400, "order-direction-invalid")
                descending = direction.tag == f"{{{DAV}}}descending"
            order_by.append((property_tag, descending, caseless))

    result_limit = None
    limit = sections.get(f"{{{DAV}}}limit")
    if limit is not None:
        if limit.attrib or (limit.text or "").strip() or len(limit) != 1 or limit[0].tag != f"{{{DAV}}}nresults" or limit[0].attrib or list(limit[0]):
            raise _SearchError(400, "search-limit-invalid")
        value = (limit[0].text or "").strip()
        if not value.isdigit() or len(value) > 9:
            raise _SearchError(400, "search-limit-invalid")
        result_limit = int(value)
    return {
        "query": query,
        "scope_href": scope_href,
        "scope_depth": scope_depth,
        "operator": operator,
        "operator_count": operator_count,
        "order_by": order_by,
        "limit": result_limit,
    }


def _resolve_search_scope(
    username: str, identity: dict, arbiter: Path, scope_href: str,
) -> tuple[Path, bool, dict | None]:
    parsed = urlsplit(urljoin(request.url, scope_href))
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc.casefold() != request.host.casefold()
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise _SearchError(409, "search-scope-outside-server", "search-scope-valid")
    path = unquote(parsed.path).rstrip("/")
    prefix = unquote(_tree_url(username)).rstrip("/")
    if path != prefix and not path.startswith(prefix + "/"):
        raise _SearchError(409, "search-scope-outside-user-tree", "search-scope-valid")
    relative = path[len(prefix):].strip("/")
    try:
        resource = _tree_path(relative)
    except ValueError as exc:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid") from exc
    if not _credential_allows_path(identity, resource):
        raise _SearchError(409, "search-scope-outside-credential", "search-scope-valid")
    if resource != arbiter and arbiter not in resource.parents:
        raise _SearchError(409, "search-scope-outside-arbiter", "search-scope-valid")
    collection = resource.is_dir() and not resource.is_symlink()
    document = None
    if resource.is_file() and not resource.is_symlink():
        try:
            document = _tree_document(resource)
        except ValueError as exc:
            raise _SearchError(409, "search-scope-invalid", "search-scope-valid") from exc
    elif not collection:
        raise _SearchError(409, "search-scope-invalid", "search-scope-valid")
    return resource, collection, document


def _search_properties(
    username: str, resource: Path, document: dict | None, *, collection: bool,
) -> dict[str, str]:
    href = _tree_url(username, _store().relative(resource), collection=collection)
    applicable = _locks_for(resource, document)
    live = _live_properties(
        resource.name if resource != _store().root else "SimpleOffice Dokumente",
        collection=collection,
        document=document,
        quota=_quota_state() if collection else None,
        lock=applicable[0][1] if applicable else None,
        href=href,
        resource=resource,
        searchable=True,
    )
    return {**live, **_dead_properties(username, resource, document)}


def _search_simple_value(properties: dict[str, str], tag: str):
    serialized = properties.get(tag)
    if serialized is None:
        return None
    try:
        element = ElementTree.fromstring(serialized)
    except ElementTree.ParseError:
        return None
    if list(element):
        return None
    value = element.text or ""
    if tag == f"{{{DAV}}}getcontentlength":
        try:
            return int(value)
        except ValueError:
            return None
    if tag in {f"{{{DAV}}}creationdate", f"{{{DAV}}}getlastmodified"}:
        try:
            parsed = (
                parsedate_to_datetime(value)
                if "," in value else datetime.fromisoformat(value.replace("Z", "+00:00"))
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None
    return unicodedata.normalize("NFC", value)


def _search_literal_value(tag: str, value: str):
    if tag == f"{{{DAV}}}getcontentlength":
        try:
            return int(value)
        except ValueError:
            return None
    if tag in {f"{{{DAV}}}creationdate", f"{{{DAV}}}getlastmodified"}:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return unicodedata.normalize("NFC", value)


def _search_evaluate(
    node: ElementTree.Element,
    properties: dict[str, str],
    *,
    collection: bool,
) -> bool | None:
    if node.tag in {f"{{{DAV}}}and", f"{{{DAV}}}or", f"{{{DAV}}}not"}:
        values = [
            _search_evaluate(child, properties, collection=collection)
            for child in node
        ]
        if node.tag == f"{{{DAV}}}not":
            return None if values[0] is None else not values[0]
        if node.tag == f"{{{DAV}}}and":
            return False if False in values else True if all(value is True for value in values) else None
        return True if True in values else False if all(value is False for value in values) else None
    if node.tag == f"{{{DAV}}}is-collection":
        return collection
    if node.tag == f"{{{DAV}}}is-defined":
        return _search_property_tag(node[0]) in properties
    property_tag = _search_property_tag(node[0])
    actual = _search_simple_value(properties, property_tag)
    if actual is None:
        return None
    literal = node[1].text or ""
    caseless = node.attrib.get("caseless", "no") == "yes"
    if node.tag == f"{{{DAV}}}like":
        if not isinstance(actual, str):
            return None
        candidate = actual.casefold() if caseless else actual
        return re.fullmatch(
            _search_like_regex(literal, caseless), candidate, flags=re.DOTALL,
        ) is not None
    expected = _search_literal_value(property_tag, literal)
    if expected is None or type(actual) is not type(expected):
        return None
    if caseless and isinstance(actual, str):
        actual, expected = actual.casefold(), expected.casefold()
    if node.tag == f"{{{DAV}}}eq":
        return actual == expected
    if node.tag == f"{{{DAV}}}lt":
        return actual < expected
    if node.tag == f"{{{DAV}}}lte":
        return actual <= expected
    if node.tag == f"{{{DAV}}}gt":
        return actual > expected
    return actual >= expected


def _search_order_compare(left: dict, right: dict, order_by: list[tuple[str, bool, bool]]) -> int:
    for tag, descending, caseless in order_by:
        first = _search_simple_value(left["properties"], tag)
        second = _search_simple_value(right["properties"], tag)
        if caseless:
            first = first.casefold() if isinstance(first, str) else first
            second = second.casefold() if isinstance(second, str) else second
        if first is None and second is None:
            continue
        if first is None:
            result = -1
        elif second is None:
            result = 1
        elif type(first) is not type(second):
            result = (str(first) > str(second)) - (str(first) < str(second))
        else:
            result = (first > second) - (first < second)
        if result:
            return -result if descending else result
    return (left["href"] > right["href"]) - (left["href"] < right["href"])


def _record_search_audit(
    username: str,
    scope: Path,
    *,
    action: str,
    depth: str,
    scanned: int,
    matched: int,
    operators: int,
    client_limit: int | None,
    reason: str = "",
) -> None:
    at = utc_now()
    details = {
        "actor": f"webdav:{username}",
        "scope": _store().relative(scope),
        "depth": depth,
        "scanned": scanned,
        "matched": matched,
        "operators": operators,
        "client_limit": client_limit,
        "reason": reason,
        "at": at,
    }
    _store().history.record(
        action,
        f"webdav:{username}",
        "webdav-search",
        hashlib.sha256(f"{username}:{at}:{uuid.uuid4()}".encode()).hexdigest(),
        details,
    )


def _search_limit_response(
    username: str,
    scope: Path,
    *,
    depth: str,
    scanned: int,
    matched: int,
    operators: int,
    client_limit: int | None,
    reason: str,
) -> Response:
    _record_search_audit(
        username, scope, action="webdav_search_limit_rejected", depth=depth,
        scanned=scanned, matched=matched, operators=operators,
        client_limit=client_limit, reason=reason,
    )
    href = _tree_url(username, _store().relative(scope), collection=scope.is_dir())
    response = (
        f'<d:response><d:href>{escape(href)}</d:href>'
        '<d:status>HTTP/1.1 507 Insufficient Storage</d:status></d:response>'
    )
    return Response(
        _propfind_multistatus([response]),
        207,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
            "X-SimpleOffice-Search-Limit": reason,
        },
    )


def _search_response(
    username: str,
    identity: dict,
    arbiter: Path,
) -> Response:
    if request.mimetype not in {"application/xml", "text/xml"}:
        return _search_error_response(
            _SearchError(415, "search-content-type-not-supported"),
        )
    try:
        query = _parse_search(request.get_data(cache=True))
        scope, scope_collection, scope_document = _resolve_search_scope(
            username, identity, arbiter, query["scope_href"],
        )
    except OverflowError as exc:
        return _search_error_response(_SearchError(413, str(exc)))
    except PermissionError:
        return _search_error_response(_SearchError(400, "xml-entities-not-allowed"))
    except _SearchError as exc:
        return _search_error_response(exc)

    mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
    mutation_lock.__enter__()
    g._webdav_mutation_lock = mutation_lock
    scope_collection = scope.is_dir() and not scope.is_symlink()
    if scope.is_file() and not scope.is_symlink():
        try:
            scope_document = _tree_document(scope)
        except ValueError:
            return _search_error_response(
                _SearchError(409, "search-scope-changed", "search-scope-valid"),
            )
    elif not scope_collection:
        return _search_error_response(
            _SearchError(409, "search-scope-changed", "search-scope-valid"),
        )
    effective_depth = query["scope_depth"] if scope_collection else "0"
    candidates = [(scope, scope_collection, scope_document)]
    try:
        candidates.extend(_propfind_members(scope, effective_depth))
    except _PropfindLimitError as exc:
        return _search_limit_response(
            username, scope, depth=effective_depth, scanned=exc.observed,
            matched=0, operators=query["operator_count"],
            client_limit=query["limit"], reason=exc.reason,
        )

    matches: list[dict] = []
    for resource, collection, document in candidates:
        properties = _search_properties(
            username, resource, document, collection=collection,
        )
        if query["operator"] is not None and _search_evaluate(
            query["operator"], properties, collection=collection,
        ) is not True:
            continue
        matches.append({
            "resource": resource,
            "collection": collection,
            "document": document,
            "properties": properties,
            "href": _tree_url(
                username, _store().relative(resource), collection=collection,
            ),
        })
    if query["order_by"]:
        matches.sort(key=functools.cmp_to_key(
            lambda left, right: _search_order_compare(left, right, query["order_by"]),
        ))
    else:
        matches.sort(key=lambda item: item["href"].casefold())
    total_matches = len(matches)
    client_limit = query["limit"]
    if total_matches > MAX_SEARCH_RESULTS and (
        client_limit is None or client_limit > MAX_SEARCH_RESULTS
    ):
        return _search_limit_response(
            username, scope, depth=effective_depth, scanned=len(candidates),
            matched=total_matches, operators=query["operator_count"],
            client_limit=client_limit, reason="result-count",
        )
    if client_limit is not None:
        matches = matches[:client_limit]

    responses: list[str] = []
    response_size = len((PROPFIND_XML_PREFIX + PROPFIND_XML_SUFFIX).encode("utf-8"))
    try:
        for match in matches:
            response_size = _append_propfind_response(
                responses,
                _prop_response(
                    match["href"],
                    match["resource"].name if match["resource"] != _store().root else "SimpleOffice Dokumente",
                    collection=match["collection"],
                    document=match["document"],
                    username=username,
                    resource=match["resource"],
                    query=query["query"],
                    searchable=True,
                ),
                response_size,
            )
    except _PropfindLimitError as exc:
        return _search_limit_response(
            username, scope, depth=effective_depth, scanned=len(candidates),
            matched=total_matches, operators=query["operator_count"],
            client_limit=client_limit, reason=exc.reason,
        )
    _record_search_audit(
        username, scope, action="webdav_search_executed", depth=effective_depth,
        scanned=len(candidates), matched=total_matches,
        operators=query["operator_count"], client_limit=client_limit,
    )
    return Response(
        _propfind_multistatus(responses),
        207,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
        },
    )


def _parse_proppatch(body: bytes) -> list[tuple[str, str, str]]:
    root = _safe_xml_root(body, f"{{{DAV}}}propertyupdate")
    operations: list[tuple[str, str, str]] = []
    for instruction in root:
        if instruction.tag not in {f"{{{DAV}}}set", f"{{{DAV}}}remove"}:
            raise ValueError("PROPPATCH only accepts set and remove instructions")
        prop_nodes = list(instruction)
        if len(prop_nodes) != 1 or prop_nodes[0].tag != f"{{{DAV}}}prop":
            raise ValueError("each PROPPATCH instruction requires exactly one DAV:prop")
        action = "set" if instruction.tag == f"{{{DAV}}}set" else "remove"
        for element in prop_nodes[0]:
            if action == "remove" and (element.attrib or list(element) or (element.text or "").strip()):
                raise ValueError("properties in a remove instruction must be empty")
            clone = ElementTree.fromstring(ElementTree.tostring(element, encoding="utf-8"))
            clone.tail = None
            language = prop_nodes[0].get("{http://www.w3.org/XML/1998/namespace}lang")
            if language and "{http://www.w3.org/XML/1998/namespace}lang" not in clone.attrib:
                clone.set("{http://www.w3.org/XML/1998/namespace}lang", language)
            serialized = ElementTree.tostring(clone, encoding="unicode", short_empty_elements=True)
            if len(serialized.encode("utf-8")) > MAX_PROPERTY_VALUE:
                raise OverflowError("a WebDAV property value is too large")
            operations.append((action, element.tag, serialized))
    if not operations:
        raise ValueError("PROPPATCH contains no property instructions")
    if len(operations) > MAX_PROPERTY_COUNT:
        raise OverflowError("PROPPATCH contains too many property instructions")
    return operations


def _live_property_value_valid(tag: str, serialized: str) -> bool:
    if tag not in MUTABLE_DAV_PROPERTIES | MICROSOFT_CLIENT_PROPERTIES | {MICROSOFT_SPECIAL_FOLDER}:
        return True
    element = ElementTree.fromstring(serialized)
    if list(element):
        return False
    if tag in MICROSOFT_CLIENT_PROPERTIES | {MICROSOFT_SPECIAL_FOLDER} and element.attrib:
        return False
    text = element.text or ""
    if tag == f"{{{DAV}}}displayname":
        return len(text.encode("utf-8")) <= 1024 and "\x00" not in text
    if tag == f"{{{DAV}}}getcontentlanguage":
        return bool(re.fullmatch(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", text.strip()))
    if len(text.encode("utf-8")) > 256 or "\x00" in text:
        return False
    if tag == MICROSOFT_SPECIAL_FOLDER:
        try:
            value = int(text, 10)
        except ValueError:
            return False
        return -(2**31) <= value < 2**31 and text.strip() == str(value)
    return True


def _proppatch_response(href: str, statuses: list[tuple[str, int]]) -> Response:
    groups: dict[int, list[str]] = {}
    for tag, status in statuses:
        groups.setdefault(status, []).append(_empty_property(tag))
    labels = {200: "OK", 403: "Forbidden", 409: "Conflict", 424: "Failed Dependency", 507: "Insufficient Storage"}
    parts = []
    for status, properties in groups.items():
        error = '<d:error><d:cannot-modify-protected-property/></d:error>' if status == 403 else ""
        parts.append(f'<d:propstat><d:prop>{"".join(properties)}</d:prop><d:status>HTTP/1.1 {status} {labels[status]}</d:status>{error}</d:propstat>')
    xml = f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:"><d:response><d:href>{escape(href)}</d:href>{"".join(parts)}</d:response></d:multistatus>'
    return Response(xml, 207, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})


def _apply_proppatch(username: str, resource: Path, document: dict | None, href: str) -> tuple[Response, bool]:
    body = request.get_data(cache=True)
    try:
        operations = _parse_proppatch(body)
    except OverflowError as exc:
        return Response(str(exc), 413), False
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml"), False
    except ValueError as exc:
        return Response(str(exc), 400), False

    protected = {
        index for index, (_, tag, _) in enumerate(operations)
        if tag in PROTECTED_DAV_PROPERTIES or (tag.startswith(f"{{{DAV}}}") and tag not in MUTABLE_DAV_PROPERTIES) or not tag.startswith("{")
    }
    conflicts = {
        index for index, (action, tag, serialized) in enumerate(operations)
        if action == "set" and not _live_property_value_valid(tag, serialized)
    }
    if protected or conflicts:
        statuses = [
            (tag, 403 if index in protected else 409 if index in conflicts else 424)
            for index, (_, tag, _) in enumerate(operations)
        ]
        return _proppatch_response(href, statuses), False

    key = _property_resource_key(username, resource, document)
    path = _properties_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    changed_names: list[str] = []
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "resources": {}})
        resources = payload.setdefault("resources", {})
        if not isinstance(resources, dict):
            resources = {}
            payload["resources"] = resources
        current = resources.get(key, {})
        proposed = dict(current) if isinstance(current, dict) else {}
        for action, tag, serialized in operations:
            before = proposed.get(tag)
            if action == "set":
                proposed[tag] = serialized
            else:
                proposed.pop(tag, None)
            if proposed.get(tag) != before:
                changed_names.append(tag)
        if len(proposed) > MAX_STORED_PROPERTIES:
            return _proppatch_response(href, [(tag, 507 if index == 0 else 424) for index, (_, tag, _) in enumerate(operations)]), False
        if changed_names:
            if proposed:
                resources[key] = proposed
            else:
                resources.pop(key, None)
            payload["version"] = 1
            atomic_json_write(path, payload)

    if changed_names:
        audit_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        _store().history.record(
            "webdav_properties_changed", f"webdav:{username}", "webdav-properties", audit_key,
            {"resource": _store().relative(resource), "properties": sorted(set(changed_names)), "changed_at": utc_now(), "actor": f"webdav:{username}"},
        )
    return _proppatch_response(href, [(tag, 200) for _, tag, _ in operations]), bool(changed_names)


def _copy_dead_properties(
    username: str,
    source: Path,
    source_document: dict | None,
    destination: Path,
    destination_document: dict | None,
) -> None:
    """Preserve dead properties on COPY as recommended by RFC 4918 section 9.8.2."""
    path = _properties_path()
    if not path.exists():
        return
    source_key = _property_resource_key(username, source, source_document)
    destination_key = _property_resource_key(username, destination, destination_document)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "resources": {}})
        resources = payload.get("resources", {})
        source_properties = resources.get(source_key, {}) if isinstance(resources, dict) else {}
        if not isinstance(source_properties, dict) or not source_properties:
            return
        resources[destination_key] = dict(source_properties)
        atomic_json_write(path, payload)
    _store().history.record(
        "webdav_properties_copied", f"webdav:{username}", "webdav-properties",
        hashlib.sha256(destination_key.encode("utf-8")).hexdigest(),
        {
            "source": _store().relative(source),
            "destination": _store().relative(destination),
            "properties": sorted(source_properties),
            "copied_at": utc_now(),
            "actor": f"webdav:{username}",
        },
    )


def _collection_lock_error(resource: Path, username: str) -> Response | None:
    """Require tokens for every explicit lock rooted inside a collection."""
    relative = _store().relative(resource)
    for stored_key, lock in _active_locks().get("locks", {}).items():
        lock_resource = str(lock.get("resource", "")).strip()
        if not lock_resource or not _relative_is_within(lock_resource, relative):
            continue
        if lock.get("username") != username or lock.get("token") != _request_token(stored_key):
            return Response("a member of the collection is locked", 423)
    return None


def _release_collection_locks_after_move(username: str, source: Path) -> None:
    """RFC 4918 section 7.6 forbids moving source locks with a resource."""
    source_relative = _store().relative(source)
    path = _locks_path()
    released: list[dict] = []
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        locks = payload.get("locks", {})
        for stored_key, lock in list(locks.items()):
            lock_resource = str(lock.get("resource", "")).strip()
            if lock.get("username") != username or not lock_resource or not _relative_is_within(lock_resource, source_relative):
                continue
            released.append(dict(lock))
            locks.pop(stored_key, None)
        if released:
            atomic_json_write(path, payload)
    for lock in released:
        _store().history.record(
            "webdav_lock_released_by_move", f"webdav:{username}", "webdav-locks",
            hashlib.sha256(f"{username}:{lock.get('resource', '')}".encode()).hexdigest(),
            {
                "resource": str(lock.get("resource", "")), "depth": str(lock.get("depth", "0")),
                "released_at": utc_now(), "actor": f"webdav:{username}",
            },
        )


def _release_file_lock_after_move(username: str, source: Path, document: dict) -> None:
    """Release an explicit source lock after MOVE while retaining target locks."""
    key = _lock_key(source, document)
    lock = _lock_for(key)
    if not lock or lock.get("username") != username:
        return
    _release_lock(key)
    _store().history.record(
        "webdav_lock_released_by_move", f"webdav:{username}", "webdav-locks",
        hashlib.sha256(f"{username}:{lock.get('resource', '')}".encode()).hexdigest(),
        {
            "resource": lock.get("resource", ""), "token": lock.get("token", ""),
            "released_at": utc_now(), "actor": f"webdav:{username}",
        },
    )


def _release_collection_locks_after_delete(username: str, source: Path) -> None:
    """Destroy every lock rooted on a successfully deleted collection member."""
    source_relative = _store().relative(source)
    path = _locks_path()
    released: list[dict] = []
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        locks = payload.get("locks", {})
        for stored_key, lock in list(locks.items()):
            lock_resource = str(lock.get("resource", "")).strip()
            if lock.get("username") != username or not lock_resource or not _relative_is_within(lock_resource, source_relative):
                continue
            released.append(dict(lock))
            locks.pop(stored_key, None)
        if released:
            atomic_json_write(path, payload)
    for lock in released:
        _store().history.record(
            "webdav_lock_destroyed_by_delete", f"webdav:{username}", "webdav-locks",
            hashlib.sha256(f"{username}:{lock.get('resource', '')}".encode()).hexdigest(),
            {
                "resource": str(lock.get("resource", "")), "depth": str(lock.get("depth", "0")),
                "deleted_at": utc_now(), "actor": f"webdav:{username}",
            },
        )


def _visible_snapshot(collection: Path) -> dict[str, dict]:
    """Return the visible regular-file tree without following unsafe nodes."""
    store = _store()
    snapshot: dict[str, dict] = {}
    for current, directories, files in os.walk(collection, followlinks=False):
        parent = Path(current)
        directories[:] = sorted(
            name for name in directories
            if name not in {CONTROL_DIR, HISTORY_DIR} and not (parent / name).is_symlink()
        )
        for name in directories:
            path = parent / name
            snapshot[store.relative(path)] = {"collection": True, "signature": "collection"}
        for name in sorted(files):
            if name == POLICY_FILE:
                continue
            path = parent / name
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            snapshot[store.relative(path)] = {
                "collection": False,
                "signature": f"{stat.st_size}:{stat.st_mtime_ns}",
            }
    return snapshot


def _new_sync_token() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def _record_sync_changes(username: str, *relative_paths: str) -> None:
    """Record mutations immediately, including remove-and-remap sequences."""
    path = _sync_path()
    if not path.exists():
        return
    normalized = sorted({value.strip("/") for value in relative_paths if value.strip("/")})
    if not normalized:
        return
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "users": {}})
        collections = payload.get("users", {}).get(username, {}).get("collections", {})
        if not isinstance(collections, dict):
            return
        for collection_key, state in collections.items():
            if not isinstance(state, dict):
                continue
            collection_relative = "" if collection_key == "." else str(collection_key)
            previous = state.get("snapshot", {}) if isinstance(state.get("snapshot"), dict) else {}
            current = dict(previous)
            for relative in normalized:
                resource = _store().root / relative
                if resource.is_dir() and not resource.is_symlink():
                    current[relative] = {"collection": True, "signature": "collection"}
                elif resource.is_file() and not resource.is_symlink():
                    stat = resource.stat()
                    current[relative] = {"collection": False, "signature": f"{stat.st_size}:{stat.st_mtime_ns}"}
                else:
                    current.pop(relative, None)
            changes = state.get("changes", []) if isinstance(state.get("changes"), list) else []
            tokens = state.get("tokens", []) if isinstance(state.get("tokens"), list) else []
            revision = int(state.get("revision", 0))
            path_revisions = state.get("path_revisions")
            if not isinstance(path_revisions, dict):
                path_revisions = {}
                for relative, info in sorted(previous.items()):
                    revision += 1
                    changes.append({
                        "revision": revision, "path": relative, "removed": False,
                        "collection": bool(info.get("collection")),
                    })
                    path_revisions[relative] = revision
            for relative in normalized:
                try:
                    Path(relative).relative_to(collection_relative) if collection_relative else Path(relative)
                except ValueError:
                    continue
                revision += 1
                token = _new_sync_token()
                changes.append({
                    "revision": revision,
                    "path": relative,
                    "removed": relative not in current,
                    "collection": bool((current.get(relative) or previous.get(relative) or {}).get("collection")),
                })
                if relative in current:
                    path_revisions[relative] = revision
                else:
                    path_revisions.pop(relative, None)
                tokens.append({"token": token, "revision": revision})
                state["token"] = token
            state["revision"] = revision
            state["snapshot"] = current
            state["path_revisions"] = path_revisions
            state["changes"] = changes[-MAX_SYNC_CHANGES:]
            minimum = state["changes"][0]["revision"] - 1 if state["changes"] else revision
            state["tokens"] = [item for item in tokens if int(item.get("revision", -1)) >= minimum][-MAX_SYNC_TOKENS:]
        atomic_json_write(path, payload)


def _sync_state(username: str, collection: Path) -> dict:
    """Reconcile one user-bound collection journal with the current disk tree."""
    path = _sync_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    collection_key = _store().relative(collection) or "."
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "users": {}})
        users = payload.setdefault("users", {})
        collections = users.setdefault(username, {}).setdefault("collections", {})
        snapshot = _visible_snapshot(collection)
        state = collections.get(collection_key)
        if not isinstance(state, dict):
            changes = []
            path_revisions = {}
            revision = 0
            for relative, info in sorted(snapshot.items()):
                revision += 1
                changes.append({
                    "revision": revision,
                    "path": relative,
                    "removed": False,
                    "collection": bool(info.get("collection")),
                })
                path_revisions[relative] = revision
            token = _new_sync_token()
            state = {
                "revision": revision,
                "token": token,
                "tokens": [{"token": token, "revision": revision}],
                "changes": changes,
                "snapshot": snapshot,
                "path_revisions": path_revisions,
            }
            collections[collection_key] = state
        else:
            previous = state.get("snapshot", {}) if isinstance(state.get("snapshot"), dict) else {}
            changes = state.get("changes", []) if isinstance(state.get("changes"), list) else []
            tokens = state.get("tokens", []) if isinstance(state.get("tokens"), list) else []
            revision = int(state.get("revision", 0))
            path_revisions = state.get("path_revisions")
            if not isinstance(path_revisions, dict):
                # A one-time, transport-only baseline makes pre-pagination
                # journals safely resumable. Existing clients merely observe
                # each current URL once more; document data is untouched.
                path_revisions = {}
                for relative, info in sorted(snapshot.items()):
                    revision += 1
                    changes.append({
                        "revision": revision,
                        "path": relative,
                        "removed": False,
                        "collection": bool(info.get("collection")),
                    })
                    path_revisions[relative] = revision
                if snapshot:
                    token = _new_sync_token()
                    tokens.append({"token": token, "revision": revision})
                    state["token"] = token
            for relative in sorted(set(previous) | set(snapshot)):
                if previous.get(relative) == snapshot.get(relative):
                    continue
                revision += 1
                token = _new_sync_token()
                changes.append({
                    "revision": revision,
                    "path": relative,
                    "removed": relative not in snapshot,
                    "collection": bool((snapshot.get(relative) or previous.get(relative) or {}).get("collection")),
                })
                if relative in snapshot:
                    path_revisions[relative] = revision
                else:
                    path_revisions.pop(relative, None)
                tokens.append({"token": token, "revision": revision})
                state["token"] = token
            state["revision"] = revision
            state["snapshot"] = snapshot
            state["path_revisions"] = path_revisions
            state["changes"] = changes[-MAX_SYNC_CHANGES:]
            minimum = state["changes"][0]["revision"] - 1 if state["changes"] else revision
            retained = [item for item in tokens if int(item.get("revision", -1)) >= minimum]
            state["tokens"] = retained[-MAX_SYNC_TOKENS:]
            if not state.get("token"):
                state["token"] = state["tokens"][-1]["token"] if state["tokens"] else _new_sync_token()
        payload["version"] = 1
        atomic_json_write(path, payload)
        return json.loads(json.dumps(state))


def _sync_token_for_revision(username: str, collection: Path, revision: int) -> str:
    """Return a persisted opaque token for an exactly processed revision."""
    path = _sync_path()
    collection_key = _store().relative(collection) or "."
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"version": 1, "users": {}})
        state = payload.get("users", {}).get(username, {}).get("collections", {}).get(collection_key)
        if not isinstance(state, dict):
            raise ValueError("sync state is unavailable")
        current_revision = int(state.get("revision", 0))
        if revision < 0 or revision > current_revision:
            raise ValueError("sync revision is unavailable")
        changes = state.get("changes", []) if isinstance(state.get("changes"), list) else []
        minimum = int(changes[0].get("revision", 0)) - 1 if changes else current_revision
        if revision < minimum:
            raise ValueError("sync revision has expired")
        tokens = state.get("tokens", []) if isinstance(state.get("tokens"), list) else []
        for item in reversed(tokens):
            if isinstance(item, dict) and int(item.get("revision", -1)) == revision:
                return str(item["token"])
        token = _new_sync_token()
        tokens.append({"token": token, "revision": revision})
        state["tokens"] = tokens[-MAX_SYNC_TOKENS:]
        atomic_json_write(path, payload)
        return token


def _collection_sync_token(username: str, collection: Path) -> str:
    """Read the mutation-maintained token without rescanning on every PROPFIND."""
    collection_key = _store().relative(collection) or "."
    state = (
        _read_json(_sync_path(), {"users": {}})
        .get("users", {}).get(username, {}).get("collections", {}).get(collection_key)
    )
    if isinstance(state, dict) and state.get("token"):
        return str(state["token"])
    return str(_sync_state(username, collection)["token"])


def _sync_if_error(username: str, identity: dict) -> Response | None:
    """Validate all RFC 4918 If conditions, including RFC 6578 tokens."""
    return _if_header_error(username, identity)


def _sync_member_in_scope(relative: str, collection: Path, level: str) -> bool:
    collection_relative = _store().relative(collection)
    try:
        nested = Path(relative).relative_to(collection_relative) if collection_relative else Path(relative)
    except ValueError:
        return False
    return nested != Path(".") and (level == "infinite" or len(nested.parts) == 1)


def _sync_limit(root: ElementTree.Element) -> int:
    nodes = root.findall(f"{{{DAV}}}limit")
    if not nodes:
        return MAX_SYNC_PAGE_RESULTS
    if len(nodes) != 1:
        raise ValueError("sync-collection accepts one limit")
    results = nodes[0].findall(f"{{{DAV}}}nresults")
    if len(results) != 1 or len(nodes[0]) != 1:
        raise ValueError("DAV:limit requires one DAV:nresults")
    value = (results[0].text or "").strip()
    if not value.isdecimal() or int(value) < 1:
        raise ValueError("DAV:nresults must be a positive integer")
    return min(int(value), MAX_SYNC_PAGE_RESULTS)


def _effective_sync_members(members: list[dict], level: str) -> list[dict]:
    """Suppress descendant tombstones while advancing their sync cursor."""
    removed_collections: dict[str, dict] = {}
    effective: list[dict] = []
    for original in sorted(members, key=lambda item: (int(item.get("revision", 0)), str(item.get("path", "")))):
        item = dict(original)
        relative = str(item["path"])
        revision = int(item.get("revision", 0))
        parent = next(
            (
                removed for path, removed in removed_collections.items()
                if level == "infinite" and relative.startswith(path + "/")
            ),
            None,
        )
        if parent is not None:
            parent["cursor_revision"] = max(int(parent.get("cursor_revision", 0)), revision)
            continue
        item["cursor_revision"] = revision
        effective.append(item)
        if item.get("removed") and item.get("collection"):
            removed_collections[relative] = item
    return effective


def _sync_member_response(username: str, item: dict, query: tuple[str, list[str]]) -> str:
    relative = str(item["path"])
    resource = _store().root / relative
    current_collection = resource.is_dir() and not resource.is_symlink()
    current_file = resource.is_file() and not resource.is_symlink()
    href = _tree_url(
        username, relative,
        collection=current_collection or (not current_file and bool(item.get("collection"))),
    )
    if item.get("removed") or (not current_collection and not current_file):
        return f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>"
    if current_collection:
        return _prop_response(
            href, resource.name, collection=True, username=username,
            resource=resource, query=query, searchable=True,
        )
    try:
        document = _tree_document(resource)
    except ValueError:
        return f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>"
    return _prop_response(
        href, resource.name, document=document, username=username,
        resource=resource, query=query, searchable=True,
    )


def _sync_limit_response(username: str, collection: Path, responses: list[str], token: str) -> Response:
    href = _tree_url(username, _store().relative(collection), collection=True)
    responses.append(
        f"<d:response><d:href>{escape(href)}</d:href>"
        "<d:status>HTTP/1.1 507 Insufficient Storage</d:status>"
        "<d:error><d:number-of-matches-within-limits/></d:error></d:response>"
    )
    xml = f'''<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}<d:sync-token>{escape(token)}</d:sync-token></d:multistatus>'''
    return Response(
        xml, 207, {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization, Depth",
            "X-SimpleOffice-Sync-Limit": "result-count",
        },
    )


def _sync_report(username: str, collection: Path) -> Response:
    if request.headers.get("Depth", "0") != "0":
        return Response("sync-collection requires Depth: 0", 400)
    body = request.get_data(cache=True)
    if len(body) > 64 * 1024:
        return Response("REPORT body is too large", 413)
    try:
        root = _safe_xml_root(body, f"{{{DAV}}}sync-collection")
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml")
    except ValueError:
        return Response("invalid sync-collection XML", 400)
    token_nodes = root.findall(f"{{{DAV}}}sync-token")
    level_nodes = root.findall(f"{{{DAV}}}sync-level")
    prop_nodes = root.findall(f"{{{DAV}}}prop")
    if len(token_nodes) != 1 or len(level_nodes) != 1 or len(prop_nodes) != 1:
        return Response("sync-token, sync-level and prop are required", 400)
    token_node, level_node = token_nodes[0], level_nodes[0]
    query = ("prop", [child.tag for child in prop_nodes[0]])
    level = (level_node.text or "").strip()
    if level not in {"1", "infinite"}:
        return Response("sync-level must be 1 or infinite", 400)
    try:
        page_limit = _sync_limit(root)
    except ValueError as exc:
        return Response(str(exc), 400)

    supplied_token = (token_node.text or "").strip()
    state = _sync_state(username, collection)
    if supplied_token:
        token_revisions = {
            str(item.get("token", "")): int(item.get("revision", -1))
            for item in state.get("tokens", []) if isinstance(item, dict)
        }
        if supplied_token not in token_revisions:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:valid-sync-token/></d:error>'
            return Response(error, 403, mimetype="application/xml")
        since = token_revisions[supplied_token]
        latest: dict[str, dict] = {}
        for change in state.get("changes", []):
            if int(change.get("revision", -1)) > since and _sync_member_in_scope(str(change.get("path", "")), collection, level):
                latest[str(change["path"])] = change
        members = list(latest.values())
    else:
        path_revisions = state.get("path_revisions", {})
        members = [
            {
                "path": relative, "removed": False,
                "collection": bool(info.get("collection")),
                "revision": int(path_revisions.get(relative, state.get("revision", 0))),
            }
            for relative, info in sorted(state.get("snapshot", {}).items())
            if _sync_member_in_scope(relative, collection, level)
        ]
    members = _effective_sync_members(members, level)
    responses: list[str] = []
    consumed = 0
    response_size = 256
    for item in members[:page_limit]:
        rendered = _sync_member_response(username, item, query)
        rendered_size = len(rendered.encode("utf-8"))
        if responses and response_size + rendered_size > MAX_PROPFIND_RESPONSE_BYTES:
            break
        if not responses and response_size + rendered_size > MAX_PROPFIND_RESPONSE_BYTES:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:number-of-matches-within-limits/></d:error>'
            return Response(error, 507, mimetype="application/xml")
        responses.append(rendered)
        response_size += rendered_size
        consumed += 1
    if consumed < len(members):
        cursor_revision = int(members[consumed - 1]["cursor_revision"])
        try:
            partial_token = _sync_token_for_revision(username, collection, cursor_revision)
        except ValueError:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:valid-sync-token/></d:error>'
            return Response(error, 403, mimetype="application/xml")
        _store().history.record(
            "webdav_sync_truncated", f"webdav:{username}", "webdav-sync",
            hashlib.sha256(f"{username}:{partial_token}".encode()).hexdigest(),
            {
                "collection": _store().relative(collection) or ".", "sync_level": level,
                "returned": consumed, "remaining": len(members) - consumed,
                "cursor_revision": cursor_revision, "actor": f"webdav:{username}",
                "at": utc_now(),
            },
        )
        return _sync_limit_response(username, collection, responses, partial_token)
    xml = f'''<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}<d:sync-token>{escape(str(state["token"]))}</d:sync-token></d:multistatus>'''
    return Response(
        xml, 207, {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store", "Vary": "Authorization, Depth",
        },
    )


@bp.route("/documents/<document_id>/libreoffice", methods=["GET", "POST"])
@login_required
def setup_document(document_id: str):
    try:
        document = _store().get_document(document_id)
        _document_path(document)
    except ValueError:
        return Response("document not found", 404)
    username = str(g.user["username"])
    generated_password = ""
    if request.method == "POST":
        action = request.form.get("action", "activate")
        if action == "revoke_all":
            revoke(username, username)
            flash("Alle WebDAV-Zugänge wurden widerrufen.")
        elif action == "revoke":
            if revoke(username, username, request.form.get("credential_id", "")):
                flash("WebDAV-Zugang wurde widerrufen.")
            else:
                flash("Der WebDAV-Zugang war bereits widerrufen.")
        else:
            try:
                generated_password = activate(
                    username, username,
                    label=request.form.get("label", "Desktop-Zugang"),
                    scope=request.form.get("scope", "write"),
                    expires_days=int(request.form.get("expires_days", "90")),
                    path_prefix=request.form.get("path_prefix", ""),
                )
            except (TypeError, ValueError) as exc:
                flash(str(exc) or "WebDAV-Zugang konnte nicht angelegt werden.")
    credentials = credentials_for(username)
    for credential in credentials:
        credential["webdav_url"] = _tree_url(
            username, credential["path_prefix"], external=True, collection=True,
        )
    configured = any(not item["expired"] for item in credentials)
    webdav_url = _resource_url(username, document, external=True)
    webdav_root_url = next(
        (item["webdav_url"] for item in credentials if not item["expired"]),
        _tree_url(username, external=True, collection=True),
    )
    return render_template(
        "documents/libreoffice.html",
        document=document,
        webdav_url=webdav_url,
        webdav_root_url=webdav_root_url,
        configured=configured,
        generated_password=generated_password,
        credentials=credentials,
        default_path_prefix="" if Path(str(document["last_path"])).parent == Path(".") else str(Path(str(document["last_path"])).parent),
        quota=_quota_state(),
    )


@bp.route("/settings/webdav", methods=["GET", "POST"])
@login_required
def setup_user_webdav():
    """Manage one user's persistent, whole-tree WebDAV credentials."""
    username = str(g.user["username"])
    generated_password = ""
    if request.method == "POST":
        action = request.form.get("action", "activate")
        if action == "revoke_all":
            revoke(username, username); flash("Alle WebDAV-Zugänge wurden widerrufen.")
        elif action == "revoke":
            flash("WebDAV-Zugang wurde widerrufen." if revoke(username, username, request.form.get("credential_id", "")) else "Der WebDAV-Zugang war bereits widerrufen.")
        elif action == "rotate":
            try:
                if request.form.get("confirm_rotation") != "ROTATE":
                    raise ValueError("Bitte die sofortige Ungültigkeit des alten Passworts bestätigen.")
                generated_password = rotate(username, username, request.form.get("credential_id", ""), int(request.form.get("expires_days", "365")))
                flash("App-Passwort ersetzt. Das alte Passwort ist ab sofort ungültig.")
            except (TypeError, ValueError) as exc:
                flash(str(exc) or "WebDAV-Passwort konnte nicht ersetzt werden.")
        else:
            try:
                generated_password = activate(username, username, label=request.form.get("label", "Allgemeiner Desktop-Zugang"), scope=request.form.get("scope", "write"), expires_days=int(request.form.get("expires_days", "365")), path_prefix="")
            except (TypeError, ValueError) as exc:
                flash(str(exc) or "WebDAV-Zugang konnte nicht angelegt werden.")
    credentials = credentials_for(username)
    for credential in credentials:
        credential["webdav_url"] = _tree_url(username, credential["path_prefix"], external=True, collection=True)
    return render_template("documents/webdav_settings.html", username=username, webdav_root_url=_tree_url(username, external=True, collection=True), generated_password=generated_password, credentials=credentials, quota=_quota_state())


@bp.route("/webdav/files/<username>", defaults={"relative_path": ""}, methods=["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "SEARCH", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
@bp.route("/webdav/files/<username>/<path:relative_path>", methods=["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "SEARCH", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
def file_tree(username: str, relative_path: str):
    """Hierarchical WebDAV namespace for desktop file managers and sync clients."""
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    if identity["username"] != username:
        return Response("not found", 404)
    g._webdav_identity = identity
    allow = "OPTIONS, PROPFIND, REPORT, SEARCH, GET, HEAD" if identity["scope"] == "read" else "OPTIONS, PROPFIND, PROPPATCH, REPORT, SEARCH, GET, HEAD, PUT, DELETE, MKCOL, COPY, MOVE, LOCK, UNLOCK"
    try:
        resource = _tree_path(relative_path)
    except ValueError:
        return Response("not found", 404)
    if not _credential_allows_path(identity, resource):
        return Response("not found", 404)
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1, 2, sync-collection", "MS-Author-Via": "DAV", "Allow": allow,
            "DASL": "<DAV:basicsearch>", "Want-Content-Digest": DIGEST_PREFERENCE,
            "Cache-Control": "private, no-store", "Vary": "Authorization",
        })
    if identity["scope"] != "write" and request.method in WRITE_METHODS:
        return _need_privileges_response(
            request.path, _missing_method_privilege(request.method), allow,
        )
    is_collection = resource.is_dir() and not resource.is_symlink()
    document = None
    if resource.is_file() and not resource.is_symlink():
        try:
            document = _tree_document(resource)
        except ValueError:
            return Response("not found", 404)

    if request.method in WRITE_METHODS:
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        sync_error = _sync_if_error(username, identity)
        if sync_error is not None:
            return sync_error

    if request.method == "REPORT":
        if not is_collection:
            return Response("sync-collection requires a collection", 400)
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        return _sync_report(username, resource)

    if request.method == "SEARCH":
        if not is_collection and document is None:
            return Response("not found", 404)
        return _search_response(username, identity, resource)

    if request.method == "PROPFIND":
        if not is_collection and document is None:
            return Response("not found", 404)
        depth = request.headers.get("Depth", "infinity").casefold()
        if depth not in {"0", "1", "infinity"}:
            return Response("PROPFIND Depth must be 0, 1 or infinity", 400)
        body = request.get_data(cache=True)
        try:
            query = _parse_propfind(body)
        except OverflowError as exc:
            return Response(str(exc), 413)
        except PermissionError:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
            return Response(error, 400, mimetype="application/xml")
        except ValueError as exc:
            return Response(str(exc), 400)

        # Keep a complete tree response consistent with the same lock used by
        # PUT, COPY, MOVE, DELETE and property mutations. The XML is fully
        # assembled before sending, so a client never receives a partial
        # success followed by a server-side resource-limit failure.
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        is_collection = resource.is_dir() and not resource.is_symlink()
        document = None
        if resource.is_file() and not resource.is_symlink():
            try:
                document = _tree_document(resource)
            except ValueError:
                return Response("not found", 404)
        elif not is_collection:
            return Response("not found", 404)
        effective_depth = depth if is_collection else "0"
        href = _tree_url(username, _store().relative(resource), collection=is_collection)
        wants_sync_token = query[0] == "propname" or f"{{{DAV}}}sync-token" in query[1]
        sync_token = _collection_sync_token(username, resource) if is_collection and wants_sync_token else ""
        responses: list[str] = []
        response_size = len((PROPFIND_XML_PREFIX + PROPFIND_XML_SUFFIX).encode("utf-8"))
        try:
            response_size = _append_propfind_response(
                responses,
                _prop_response(href, resource.name if resource != _store().root else "SimpleOffice Dokumente", collection=is_collection, document=document, sync_token=sync_token, username=username, resource=resource, query=query, searchable=True),
                response_size,
            )
            for child, child_is_collection, child_document in _propfind_members(resource, effective_depth):
                child_href = _tree_url(
                    username, _store().relative(child), collection=child_is_collection,
                )
                response_size = _append_propfind_response(
                    responses,
                    _prop_response(
                        child_href,
                        child.name,
                        collection=child_is_collection,
                        document=child_document,
                        username=username,
                        resource=child,
                        query=query,
                        searchable=True,
                    ),
                    response_size,
                )
            xml = _propfind_multistatus(responses)
        except _PropfindLimitError as exc:
            return _propfind_limit_response(username, resource, exc)
        return Response(
            xml,
            207,
            {
                "Content-Type": "application/xml; charset=utf-8",
                "Cache-Control": "private, no-store",
                "Vary": "Authorization, Depth",
            },
        )

    if request.method in {"GET", "HEAD"}:
        if document is None:
            return Response("not found", 404)
        return _download_response(
            resource, username, document,
            mimetypes.guess_type(resource.name)[0] or "application/octet-stream",
        )

    key = _lock_key(resource, document)
    lock_error = _require_lock(resource, document, username)
    if lock_error is not None and request.method not in {"LOCK", "UNLOCK", "COPY"}:
        return lock_error

    if request.method == "PROPPATCH":
        if not is_collection and document is None:
            return Response("not found", 404)
        precondition_error = _http_precondition_error(username, resource, document)
        if precondition_error is not None:
            return precondition_error
        if document is not None:
            try:
                _store()._require_document_editable(document)
            except ValueError as exc:
                return Response(str(exc), 423)
        href = _tree_url(username, _store().relative(resource), collection=is_collection)
        response, changed = _apply_proppatch(username, resource, document, href)
        if changed:
            _record_sync_changes(username, _store().relative(resource))
        return response

    if request.method == "MKCOL":
        if resource.exists():
            return Response("resource already exists", 405)
        if request.get_data():
            return Response("extended MKCOL bodies are not supported", 415)
        precondition_error = _http_precondition_error(username, resource, None)
        if precondition_error is not None:
            return precondition_error
        name_error = _portable_name_error(username, resource)
        if name_error is not None:
            return name_error
        try:
            _store().create_collection(_store().relative(resource), f"webdav:{username}")
        except ValueError:
            return Response("parent collection does not exist", 409)
        _record_sync_changes(username, _store().relative(resource))
        return Response("", 201)

    if request.method == "LOCK":
        name_error = _portable_name_error(username, resource)
        if name_error is not None:
            return name_error
        return _lock_request(username, resource, document, request.url)

    if request.method == "UNLOCK":
        token, token_error = _unlock_token()
        if token_error is not None:
            return token_error
        existing = _lock_for(key)
        if not existing or existing.get("token") != token or existing.get("username") != username:
            return Response("lock token does not match", 409)
        _release_lock(key)
        _record_lock_audit("webdav_lock_released", username, resource, existing)
        return Response("", 204)

    if request.method == "PUT":
        current_etag = _etag(document) if document else ""
        if document:
            precondition_error = _http_precondition_error(username, resource, document)
            if precondition_error is not None:
                return precondition_error
            if_match = request.headers.get("If-Match", "")
            if not _request_token(key) and not if_match:
                return Response("existing resources require If-Match or a lock token", 428, {"ETag": current_etag})
            content = request.get_data()
            digest_error = _verify_content_digest(content, username, resource)
            if digest_error is not None:
                return digest_error
            quota_error = _check_quota(username, "PUT", resource, len(content) - resource.stat().st_size)
            if quota_error is not None:
                return quota_error
            scan_error = _webdav_upload_scan_error(content, username, resource)
            if scan_error is not None:
                return scan_error
            try:
                updated = _store().replace_content(document["document_id"], content, f"webdav:{username}", expected_sha256=_etag_value(current_etag), max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]))
            except ValueError as exc:
                status = 412 if "changed since" in str(exc) else 423 if "locked" in str(exc) or "staged" in str(exc) else 400
                return Response(str(exc), status)
            if _etag(updated) != current_etag:
                _record_sync_changes(username, _store().relative(resource))
            return Response("", 204, _stored_integrity_headers(updated))
        if is_collection:
            return Response("cannot PUT a collection", 405)
        precondition_error = _http_precondition_error(username, resource, None)
        if precondition_error is not None:
            return precondition_error
        name_error = _portable_name_error(username, resource)
        if name_error is not None:
            return name_error
        content = request.get_data()
        digest_error = _verify_content_digest(content, username, resource)
        if digest_error is not None:
            return digest_error
        quota_error = _check_quota(username, "PUT", resource, len(content))
        if quota_error is not None:
            return quota_error
        scan_error = _webdav_upload_scan_error(content, username, resource)
        if scan_error is not None:
            return scan_error
        try:
            created = _store().create_document_at(_store().relative(resource), content, f"webdav:{username}", max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]))
        except FileExistsError:
            return Response("resource already exists", 412)
        except ValueError as exc:
            return Response(str(exc), 409)
        old_lock = _lock_for(key)
        if old_lock:
            _release_lock(key)
            _save_lock(
                created["document_id"], username, old_lock["token"], _timeout_seconds(), old_lock.get("owner", ""),
                href=str(old_lock.get("href", request.url)), depth=str(old_lock.get("depth", "0")),
                resource=_store().relative(resource),
            )
        _record_sync_changes(username, _store().relative(resource))
        return Response("", 201, _stored_integrity_headers(created))

    if request.method == "DELETE":
        if document:
            precondition_error = _http_precondition_error(username, resource, document)
            if precondition_error is not None:
                return precondition_error
            try:
                _store().soft_delete_document(document["document_id"], f"webdav:{username}")
            except ValueError as exc:
                return Response(str(exc), 423)
            _release_lock(key)
            _record_sync_changes(username, _store().relative(resource))
            return Response("", 204)
        if is_collection:
            if resource == _store().root:
                return Response("the WebDAV root cannot be deleted", 403)
            if _credential_is_boundary(identity, resource):
                return Response("the credential lacks access to the parent collection", 403)
            depth = request.headers.get("Depth", "infinity").lower()
            if depth != "infinity":
                return Response("collection DELETE requires Depth: infinity", 400)
            precondition_error = _http_precondition_error(username, resource, None)
            if precondition_error is not None:
                return precondition_error
            collection_lock_error = _collection_lock_error(resource, username)
            if collection_lock_error is not None:
                return collection_lock_error
            try:
                result = _store().soft_delete_collection(
                    _store().relative(resource), f"webdav:{username}",
                )
            except OSError:
                return _quota_error(username, "DELETE", resource, 0, "sufficient-disk-space")
            except (RuntimeError, ValueError) as exc:
                status = 507 if "too many" in str(exc) or "nesting depth" in str(exc) else 423 if "locked" in str(exc) or "staged" in str(exc) else 409
                return Response(str(exc), status)
            changed_paths = []
            source_relative = str(result["path"])
            for nested in result["directories_relative"]:
                changed_paths.append(source_relative if nested == Path(".") else str(Path(source_relative) / nested))
            changed_paths.extend(str(item.get("deleted_from", "")) for item in result["resources"])
            _release_collection_locks_after_delete(username, resource)
            _record_sync_changes(username, *changed_paths)
            return Response("", 204)
        return Response("not found", 404)

    if request.method in {"COPY", "MOVE"}:
        if document is None and not is_collection:
            return Response("not found", 404)
        if resource == _store().root:
            return Response("the WebDAV root cannot be copied or moved", 403)
        if request.method == "MOVE" and _credential_is_boundary(identity, resource):
            return Response("the credential lacks access to the parent collection", 403)
        overwrite = request.headers.get("Overwrite", "T").upper()
        if overwrite not in {"T", "F"}:
            return Response("Overwrite must be T or F", 400)
        depth = request.headers.get("Depth", "infinity").lower()
        if request.method == "COPY" and is_collection and depth not in {"0", "infinity"}:
            return Response("collection COPY requires Depth: 0 or infinity", 400)
        if request.method == "MOVE" and is_collection and depth != "infinity":
            return Response("collection MOVE requires Depth: infinity", 400)
        precondition_error = _http_precondition_error(username, resource, document)
        if precondition_error is not None:
            return precondition_error
        current_etag = _etag(document) if document is not None else ""
        try:
            destination, destination_relative = _destination(username, identity)
        except PermissionError:
            return Response("destination is outside the authenticated WebDAV tree", 502)
        except ValueError as exc:
            return Response(str(exc), 400)
        name_error = _portable_name_error(
            username,
            destination,
            exclude=resource if request.method == "MOVE" else None,
        )
        if name_error is not None:
            return name_error
        replacing_document = None
        if destination == resource:
            status = 403 if request.method == "MOVE" else 412
            return Response("source and destination are the same resource", status)
        if destination.exists():
            if overwrite == "F":
                return Response("destination exists and Overwrite is F", 412)
            if is_collection or destination.is_symlink() or not destination.is_file():
                return Response("only an existing regular file can be replaced by COPY or MOVE", 412)
            try:
                replacing_document = _tree_document(destination)
            except ValueError:
                return Response("destination is not an available managed document", 409)
            destination_key = _lock_key(destination, replacing_document)
            destination_etag = _etag(replacing_document)
            if not _request_token(destination_key) and not _request_etag(destination_key, destination_etag):
                return Response(
                    "replacing an existing destination requires its tagged DAV If ETag or lock token",
                    428, {"ETag": destination_etag, "Cache-Control": "private, no-cache"},
                )
            destination_lock = _require_lock(destination, replacing_document, username)
            if destination_lock is not None:
                destination_lock.headers.update({"ETag": destination_etag, "Cache-Control": "private, no-cache"})
                return destination_lock
        if not destination.parent.is_dir():
            return Response("destination parent does not exist", 409)
        if replacing_document is None:
            destination_lock = _require_lock(destination, None, username)
            if destination_lock is not None:
                return destination_lock
        manifest = None
        if is_collection:
            if request.method == "MOVE":
                collection_lock_error = _collection_lock_error(resource, username)
                if collection_lock_error is not None:
                    return collection_lock_error
            try:
                manifest = _store().collection_manifest(
                    _store().relative(resource), f"webdav:{username}",
                    depth=depth if request.method == "COPY" else "infinity",
                )
            except ValueError as exc:
                status = 507 if "too many" in str(exc) or "nesting depth" in str(exc) else 423 if "locked" in str(exc) or "staged" in str(exc) else 409
                return Response(str(exc), status)
        if request.method == "COPY":
            if replacing_document is not None:
                growth = 0
            elif manifest is not None and depth == "infinity":
                growth = int(manifest["total_bytes"])
            else:
                growth = resource.stat().st_size if document else 0
            quota_error = _check_quota(username, "COPY", destination, growth)
            if quota_error is not None:
                return quota_error
        try:
            if is_collection and request.method == "COPY":
                result = _store().copy_collection(
                    _store().relative(resource), destination_relative, f"webdav:{username}", depth=depth,
                )
                copied_directories = result["directories_relative"]
                for nested in copied_directories:
                    source_collection = resource if nested == Path(".") else resource / nested
                    destination_collection = destination if nested == Path(".") else destination / nested
                    _copy_dead_properties(username, source_collection, None, destination_collection, None)
                for item in result["resources"]:
                    _copy_dead_properties(
                        username, item["source"], item["source_document"],
                        item["destination"], item["destination_document"],
                    )
            elif is_collection:
                result = _store().move_collection(
                    _store().relative(resource), destination_relative, f"webdav:{username}",
                )
                _release_collection_locks_after_move(username, resource)
            elif request.method == "COPY" and replacing_document is not None:
                result = _store().replace_document_via_copy(
                    document["document_id"], replacing_document["document_id"], f"webdav:{username}",
                    expected_source_sha256=_etag_value(current_etag),
                    expected_destination_sha256=_etag_value(destination_etag),
                    max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
                )
            elif request.method == "COPY":
                result = _store().copy_document(document["document_id"], destination_relative, f"webdav:{username}")
                _copy_dead_properties(username, resource, document, destination, result)
            elif replacing_document is not None:
                replacement = _store().replace_document_via_move(
                    document["document_id"], replacing_document["document_id"], f"webdav:{username}",
                    expected_source_sha256=_etag_value(current_etag),
                    expected_destination_sha256=_etag_value(destination_etag),
                    max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
                )
                result = replacement["document"]
            else:
                with exclusive_file_lock(_store().control / ".document-content.lock"):
                    result = _store().move_document(document["document_id"], _store().relative(destination.parent), f"webdav:{username}", destination_name=destination.name)
        except OSError:
            return _quota_error(username, request.method, destination, 0, "sufficient-disk-space")
        except (FileExistsError, RuntimeError, ValueError) as exc:
            message = str(exc)
            status = 507 if "too many" in message or "nesting depth" in message or "rollback failed" in message else 413 if "upload size limit" in message else 412 if "changed since" in message else 423 if "locked" in message or "staged" in message else 403 if "itself" in message else 409
            return Response(str(exc), status)
        changed_paths: list[str] = []
        if is_collection:
            for nested in result["directories_relative"]:
                destination_member = destination if nested == Path(".") else destination / nested
                changed_paths.append(_store().relative(destination_member))
                if request.method == "MOVE":
                    source_member = resource if nested == Path(".") else resource / nested
                    changed_paths.append(_store().relative(source_member))
            for item in result["resources"]:
                if request.method == "COPY":
                    changed_paths.append(_store().relative(item["destination"]))
                else:
                    changed_paths.extend([item["before"], item["after"]])
        if request.method == "COPY":
            _record_sync_changes(username, *(changed_paths or [destination_relative]))
        else:
            _record_sync_changes(username, *(changed_paths or [_store().relative(resource), destination_relative]))
            if not is_collection:
                _release_file_lock_after_move(username, resource, document)
        headers = {"Location": _tree_url(username, destination_relative, collection=is_collection)}
        if not is_collection:
            headers.update(_stored_integrity_headers(result))
            headers["Location"] = _tree_url(username, destination_relative)
            headers["Content-Location"] = headers["Location"]
        return Response("", 204 if replacing_document is not None else 201, headers)

    return Response("method not allowed", 405, {"Allow": allow})


@bp.route(
    "/webdav/principals/<username>/",
    defaults={"principal_id": ""},
    methods=["OPTIONS", "PROPFIND"],
)
@bp.route(
    "/webdav/principals/<username>/<principal_id>",
    methods=["OPTIONS", "PROPFIND"],
)
def principal_resource(username: str, principal_id: str):
    """Expose only the authenticated user's stable, read-only principal resource."""
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    if identity["username"] != username or principal_id not in {"", "self"}:
        return Response("not found", 404)
    g._webdav_identity = identity
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1", "Allow": "OPTIONS, PROPFIND",
            "Cache-Control": "private, no-store",
        })
    depth = request.headers.get("Depth", "0")
    if depth not in {"0", "1"}:
        return Response("principal PROPFIND requires Depth: 0 or 1", 403)
    try:
        query = _parse_propfind(request.get_data(cache=True))
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml")
    except ValueError as exc:
        return Response(str(exc), 400)
    collection = principal_id == ""
    href = _principal_url(username, collection=collection)
    responses = [
        _principal_prop_response(
            href, username, collection=collection, query=query,
        )
    ]
    if collection and depth == "1":
        principal = _principal_url(username)
        responses.append(
            _principal_prop_response(
                principal, username, collection=False, query=query,
            )
        )
    xml = f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}</d:multistatus>'
    return Response(
        xml,
        207,
        {
            "Content-Type": "application/xml; charset=utf-8",
            "Cache-Control": "private, no-store",
            "Vary": "Authorization, Depth",
        },
    )


@bp.route("/webdav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND"])
@bp.route("/webdav/<path:path>", methods=["OPTIONS", "PROPFIND", "PROPPATCH", "GET", "HEAD", "PUT", "LOCK", "UNLOCK"])
def endpoint(path: str):
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    username = identity["username"]
    g._webdav_identity = identity
    allow = "OPTIONS, PROPFIND, GET, HEAD" if identity["scope"] == "read" else "OPTIONS, PROPFIND, PROPPATCH, GET, HEAD, PUT, LOCK, UNLOCK"
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1, 2", "MS-Author-Via": "DAV", "Allow": allow,
            "Want-Content-Digest": DIGEST_PREFERENCE,
        })
    if identity["scope"] != "write" and request.method in WRITE_METHODS:
        return _need_privileges_response(
            request.path, _missing_method_privilege(request.method), allow,
        )

    parts = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in parts) or (len(parts) >= 2 and parts[1] != username):
        return Response("not found", 404)
    document = None
    if len(parts) == 3 and parts[0] == "documents" and "--" in parts[2]:
        document_id, requested_name = parts[2].split("--", 1)
        try:
            document = _store().get_document(document_id)
            document_path = _document_path(document)
        except ValueError:
            return Response("not found", 404)
        if requested_name != document_path.name:
            return Response("not found", 404)
        if not _credential_allows_path(identity, document_path):
            return Response("not found", 404)

    if request.method == "PROPFIND":
        depth = request.headers.get("Depth", "0")
        if depth not in {"0", "1"}:
            return Response("finite Depth required", 403)
        try:
            query = _parse_propfind(request.get_data(cache=True))
        except OverflowError as exc:
            return Response(str(exc), 413)
        except PermissionError:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
            return Response(error, 400, mimetype="application/xml")
        except ValueError as exc:
            return Response(str(exc), 400)
        responses: list[str] = []
        if not parts:
            responses.append(_prop_response(request.path, "SimpleOffice4Me", collection=True, query=query))
        elif parts == ["documents", username]:
            responses.append(_prop_response(request.path, "SimpleOffice Dokumente", collection=True, username=username, resource=_store().root, query=query))
            if depth == "1":
                for item in _store().list_documents():
                    try:
                        item_path = _document_path(item)
                    except ValueError:
                        continue
                    if not _credential_allows_path(identity, item_path):
                        continue
                    responses.append(_prop_response(_resource_url(username, item), Path(item["last_path"]).name, document=item, username=username, resource=item_path, query=query))
        elif document is not None:
            responses.append(_prop_response(request.path, document_path.name, document=document, username=username, resource=document_path, query=query))
        else:
            return Response("not found", 404)
        return Response(f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}</d:multistatus>', 207, mimetype="application/xml")

    if document is None:
        return Response("not found", 404)
    if request.method in WRITE_METHODS:
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        if_error = _if_header_error(username, identity)
        if if_error is not None:
            return if_error
    if request.method == "PROPPATCH":
        lock_error = _require_lock(document_path, document, username)
        if lock_error is not None:
            return lock_error
        precondition_error = _http_precondition_error(username, document_path, document)
        if precondition_error is not None:
            return precondition_error
        try:
            _store()._require_document_editable(document)
        except ValueError as exc:
            return Response(str(exc), 423)
        response, changed = _apply_proppatch(username, document_path, document, request.path)
        if changed:
            _record_sync_changes(username, _store().relative(document_path))
        return response
    current_etag = _etag(document)
    common_headers = {"ETag": current_etag, "Accept-Ranges": "bytes", "Cache-Control": "private, no-cache"}
    if request.method in {"GET", "HEAD"}:
        return _download_response(document_path, username, document, "application/octet-stream")
    if request.method == "LOCK":
        response = _lock_request(username, document_path, document, request.url)
        response.headers.update(common_headers)
        return response
    if request.method == "UNLOCK":
        token, token_error = _unlock_token()
        if token_error is not None:
            return token_error
        lock_path = _locks_path()
        with exclusive_file_lock(lock_path.with_suffix(".lock")):
            payload = _active_locks()
            existing = payload["locks"].get(document["document_id"])
            if not existing or existing.get("token") != token or existing.get("username") != username:
                return Response("lock token does not match", 409)
            payload["locks"].pop(document["document_id"], None)
            atomic_json_write(lock_path, payload)
        _record_lock_audit("webdav_lock_released", username, document_path, existing)
        return Response("", 204)
    if request.method == "PUT":
        lock_error = _require_lock(document_path, document, username)
        if lock_error is not None:
            lock_error.headers.update(common_headers)
            return lock_error
        precondition_error = _http_precondition_error(username, document_path, document)
        if precondition_error is not None:
            return precondition_error
        content = request.get_data()
        digest_error = _verify_content_digest(content, username, document_path)
        if digest_error is not None:
            return digest_error
        quota_error = _check_quota(username, "PUT", document_path, len(content) - document_path.stat().st_size)
        if quota_error is not None:
            return quota_error
        scan_error = _webdav_upload_scan_error(content, username, document_path)
        if scan_error is not None:
            return scan_error
        try:
            updated = _store().replace_content(
                document["document_id"], content, f"webdav:{username}",
                expected_sha256=_etag_value(current_etag), max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
            )
        except ValueError as exc:
            message = str(exc)
            status = 412 if "changed since" in message else 423 if "locked" in message or "staged" in message else 400
            return Response(str(exc), status, common_headers)
        return Response("", 204, _stored_integrity_headers(updated))
    return Response("method not allowed", 405)
