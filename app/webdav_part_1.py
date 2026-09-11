"""WebDAV implementation part 1."""
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
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
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
from .safe_paths import resolve_file_under
from .ssh_keys import add_key, keys_for, revoke_key
from .virtual_filesystem import VirtualFileSystem
from .db import get_db


bp = Blueprint("webdav", "app.webdav")
DAV = "DAV:"
MICROSOFT_DAV = "urn:schemas-microsoft-com:"
MICROSOFT_OFFICE = "urn:schemas-microsoft-com:office:office"
ElementTree.register_namespace("Z", MICROSOFT_DAV)
ElementTree.register_namespace("Office", MICROSOFT_OFFICE)


def _vfs() -> VirtualFileSystem:
    return VirtualFileSystem.from_environment(current_app.config["DOCUMENT_ROOT"])


def _document_access_context(username: str) -> tuple[VirtualFileSystem, list[str], bool]:
    users = [str(row[0]) for row in get_db().execute("SELECT username FROM user ORDER BY username").fetchall()]
    configured = {
        value.strip() for value in os.environ.get("SIMPLEOFFICE_DOCUMENT_ADMINS", "").split(",")
        if value.strip()
    }
    bootstrap_admin = len(users) == 1 and users[0] == username
    administrators = configured | ({username} if bootstrap_admin else set())
    return VirtualFileSystem(current_app.config["DOCUMENT_ROOT"], administrators), users, username in administrators


def _access_folders(vfs: VirtualFileSystem, username: str, administrator: bool) -> list[str]:
    folders = ["."]
    for parent, names, _files in os.walk(vfs.root, followlinks=False):
        names[:] = sorted(
            [name for name in names if name not in {CONTROL_DIR, HISTORY_DIR} and not (Path(parent) / name).is_symlink()],
            key=str.casefold,
        )
        for name in names:
            relative = vfs.relative(Path(parent) / name)
            if administrator or vfs.allows(username, relative, "manage"):
                folders.append(relative)
            if len(folders) >= 2_000:
                return folders
    return folders


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
MAX_PORTABLE_NAME_BYTES = 200
PROPFIND_XML_PREFIX = '<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">'
PROPFIND_XML_SUFFIX = "</d:multistatus>"


class _PropfindLimitError(Exception):
    def __init__(self, reason: str, observed: int, limit: int):
        super().__init__(reason)
        self.reason = reason
        self.observed = observed
        self.limit = limit


class _SearchError(Exception):
    def __init__(self, status: int, message: str, condition: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.condition = condition


def _quota_state() -> dict[str, int] | None:
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
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def credentials_for(username: str) -> list[dict]:
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
        with _credential_usage_cache_lock:
            _credential_usage_cache.pop(cache_key, None)


def _normalize_credential_prefix(value: str) -> str:
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


def authenticate_password(username: str, password: str, *, record_use: bool = False) -> dict | None:
    users = _read_json(_credentials_path(), {"users": {}}).get("users", {})
    value = users.get(username) if isinstance(users, dict) else None
    records = _credential_records(value)
    selector = password.partition(".")[0] if "." in password else ""
    if selector:
        selected = [record for record in records if record.get("credential_id") == selector]
        candidates = selected or [record for record in records if record.get("credential_id") == "legacy"]
    else:
        candidates = records
    for record in candidates:
        if _expired(record):
            continue
        try:
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record["salt"]), n=2**14, r=8, p=1)
            expected = bytes.fromhex(record["hash"])
        except (KeyError, ValueError):
            continue
        if hmac.compare_digest(actual, expected):
            identity = {
                "username": username,
                "credential_id": str(record.get("credential_id", "legacy")),
                "scope": "read" if record.get("scope") == "read" else "write",
                "path_prefix": str(record.get("path_prefix", "")).strip(),
            }
            if record_use:
                _record_credential_use(identity["username"], identity["credential_id"])
            return identity
    return None


def _authenticate() -> dict | None:
    supplied = request.authorization
    if not supplied or supplied.type.casefold() != "basic" or not supplied.username or not supplied.password:
        return None
    return authenticate_password(supplied.username, supplied.password, record_use=True)


def _unauthorized() -> Response:
    return Response("WebDAV authentication required", 401, {"WWW-Authenticate": 'Basic realm="SimpleOffice4Me Documents", charset="UTF-8"'})


def _need_privileges_response(href: str, privilege: str, allow: str) -> Response:
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
    store = _store()
    try:
        return resolve_file_under(store.root, str(document.get("last_path", "")))
    except (OSError, ValueError) as exc:
        raise ValueError("document unavailable") from exc


def _etag(document: dict) -> str:
    path = _document_path(document)
    return f'"{sha256_file(path)}"'


def _stored_integrity_headers(document: dict) -> dict[str, str]:
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

__all__ = [name for name in globals() if not name.startswith("__")]
