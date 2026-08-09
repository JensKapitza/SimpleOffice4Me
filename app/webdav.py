"""Writable, versioned WebDAV endpoint for LibreOffice remote editing."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .document_store import CONTROL_DIR, HISTORY_DIR, POLICY_FILE, DocumentStore, atomic_json_write, sha256_file, utc_now
from .file_lock import exclusive_file_lock


bp = Blueprint("webdav", __name__)
DAV = "DAV:"


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
WRITE_METHODS = {"PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK", "PROPPATCH"}
MAX_SYNC_CHANGES = 4096
MAX_SYNC_TOKENS = 512
MAX_PROPERTY_BODY = 64 * 1024
MAX_PROPERTY_COUNT = 64
MAX_STORED_PROPERTIES = 128
MAX_PROPERTY_VALUE = 16 * 1024
MAX_PROPERTY_NODES = 256
MAX_BYTE_RANGES = 8
DOWNLOAD_CHUNK_SIZE = 64 * 1024
MAX_DIGEST_FIELD_BYTES = 2048
DIGEST_ALGORITHMS = {
    "sha-256": (hashlib.sha256, 32, 10),
    "sha-512": (hashlib.sha512, 64, 9),
}
DIGEST_PREFERENCE = "sha-512=9, sha-256=10"
PROTECTED_DAV_PROPERTIES = {
    f"{{{DAV}}}{name}" for name in (
        "creationdate", "getcontentlength",
        "getcontenttype", "getetag", "getlastmodified", "lockdiscovery",
        "quota-available-bytes", "quota-used-bytes", "resourcetype",
        "supportedlock", "supported-report-set", "sync-token",
    )
}
MUTABLE_DAV_PROPERTIES = {f"{{{DAV}}}displayname", f"{{{DAV}}}getcontentlanguage"}


def _quota_state() -> dict[str, int] | None:
    """Return repeatable RFC 4331 accounting for the visible managed tree."""
    limit = max(0, int(current_app.config.get("WEBDAV_QUOTA_BYTES", 0)))
    if not limit:
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
    return {
        "limit": limit,
        "used": used,
        "available": min(max(0, limit - used), physical_free),
        "physical_free": physical_free,
    }


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


def credentials_for(username: str) -> list[dict]:
    """Return display-safe credential metadata without password material."""
    users = _read_json(_credentials_path(), {"users": {}}).get("users", {})
    value = users.get(username) if isinstance(users, dict) else None
    result = []
    for record in _credential_records(value):
        result.append({
            "credential_id": str(record.get("credential_id", "")),
            "label": str(record.get("label", "Desktop-Zugang")),
            "scope": "read" if record.get("scope") == "read" else "write",
            "created_at": str(record.get("created_at", "")),
            "expires_at": str(record.get("expires_at", "")),
            "expired": _expired(record),
        })
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def activate(username: str, actor: str, *, label: str = "Desktop-Zugang", scope: str = "write", expires_days: int = 90) -> str:
    """Create an independently revocable WebDAV app password and return it once."""
    label = " ".join(label.split()).strip()
    if not label or len(label) > 80 or any(ord(character) < 32 for character in label):
        raise ValueError("Bezeichnung muss 1 bis 80 druckbare Zeichen enthalten.")
    if scope not in {"read", "write"}:
        raise ValueError("Unbekannter WebDAV-Rechteumfang.")
    if isinstance(expires_days, bool) or not 1 <= int(expires_days) <= 365:
        raise ValueError("Gültigkeit muss zwischen 1 und 365 Tagen liegen.")
    expires_days = int(expires_days)
    credential_id = secrets.token_hex(12)
    password = f"{credential_id}.{secrets.token_urlsafe(24)}"
    salt = os.urandom(16)
    record = {
        "credential_id": credential_id,
        "label": label,
        "scope": scope,
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
        {key: record[key] for key in ("credential_id", "label", "scope", "created_at", "expires_at")},
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
            {"credential_id": record.get("credential_id", ""), "label": record.get("label", ""), "scope": record.get("scope", "write"), "revoked_at": utc_now()},
        )
    return bool(revoked)


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
            return {
                "username": supplied.username,
                "credential_id": str(record.get("credential_id", "legacy")),
                "scope": "read" if record.get("scope") == "read" else "write",
            }
    return None


def _unauthorized() -> Response:
    return Response("WebDAV authentication required", 401, {"WWW-Authenticate": 'Basic realm="SimpleOffice4Me Documents", charset="UTF-8"'})


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
    if value.strip() == "*":
        return True
    current = _etag_value(current_etag)
    for raw in value.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        is_weak = candidate.startswith("W/")
        if not weak and is_weak:
            continue
        encoded = candidate[2:].strip() if is_weak else candidate
        if len(encoded) < 2 or not encoded.startswith('"') or not encoded.endswith('"'):
            continue
        if _etag_value(candidate) == current:
            return True
    return False


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


def _tree_document(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("document unavailable")
    return _store().get_document(path)


def _lock_key(path: Path, document: dict | None = None) -> str:
    if document:
        return str(document["document_id"])
    relative = _store().relative(path)
    return "unmapped:" + hashlib.sha256(relative.encode("utf-8")).hexdigest()


def _destination(username: str) -> tuple[Path, str]:
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
    return destination, _store().relative(destination)


def _lock_for(key: str) -> dict | None:
    return _active_locks().get("locks", {}).get(key)


def _require_lock(key: str, username: str) -> Response | None:
    lock = _lock_for(key)
    token = _request_token()
    if lock and (lock.get("token") != token or lock.get("username") != username):
        return Response("resource is locked", 423)
    return None


def _release_lock(key: str) -> None:
    path = _locks_path()
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _active_locks()
        payload.get("locks", {}).pop(key, None)
        atomic_json_write(path, payload)


def _active_locks() -> dict:
    now = datetime.now(timezone.utc)
    payload = _read_json(_locks_path(), {"locks": {}})
    locks = payload.setdefault("locks", {})
    locks = {key: value for key, value in locks.items() if datetime.fromisoformat(value["expires_at"]).astimezone(timezone.utc) > now}
    payload["locks"] = locks
    return payload


def _request_token() -> str:
    values = " ".join((request.headers.get("If", ""), request.headers.get("Lock-Token", "")))
    match = re.search(r"opaquelocktoken:[0-9a-fA-F-]+", values)
    return match.group(0) if match else ""


def _save_lock(
    document_id: str,
    username: str,
    token: str,
    timeout_seconds: int,
    owner: str = "",
    *,
    href: str = "",
    depth: str = "0",
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
        tokens = re.findall(r"opaquelocktoken:[0-9a-fA-F-]+", request.headers.get("If", ""))
        if len(tokens) != 1 or not existing or existing.get("token") != tokens[0] or existing.get("username") != username:
            error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:lock-token-matches-request-uri/></d:error>'
            return Response(error, 412, mimetype="application/xml")
        lock = _save_lock(
            key, username, tokens[0], _timeout_seconds(), existing.get("owner", ""),
            href=str(existing.get("href", href)), depth=str(existing.get("depth", "0")),
        )
        _record_lock_audit("webdav_lock_refreshed", username, resource, lock)
        return Response(_lock_xml(lock, href), 200, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})

    depth = request.headers.get("Depth", "infinity").casefold()
    if depth not in {"0", "infinity"}:
        return Response("LOCK Depth must be 0 or infinity", 400)
    if resource.is_dir() and depth == "infinity":
        return Response("recursive collection locks are not implemented", 501)

    try:
        owner = _parse_lock_body(body)
    except OverflowError as exc:
        return Response(str(exc), 413)
    except PermissionError:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:no-external-entities/></d:error>'
        return Response(error, 400, mimetype="application/xml")
    except ValueError as exc:
        return Response(str(exc), 400)
    if existing:
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
            _save_lock(provisional_key, username, token, _timeout_seconds(), owner, href=href, depth="0")
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
        lock = _save_lock(key, username, token, _timeout_seconds(), owner, href=href, depth="0" if not resource.is_dir() else depth)
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
    properties = _read_json(_properties_path(), {"resources": {}}).get("resources", {}).get(key, {})
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


def _live_properties(
    display_name: str,
    *,
    collection: bool,
    document: dict | None,
    sync_token: str = "",
    quota: dict[str, int] | None = None,
    lock: dict | None = None,
    href: str = "",
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
    }
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
    path = _document_path(document or {})
    stat = path.stat()
    values.update({
        f"{{{DAV}}}resourcetype": _xml_element(f"{{{DAV}}}resourcetype"),
        f"{{{DAV}}}getcontentlength": _xml_element(f"{{{DAV}}}getcontentlength", str(stat.st_size)),
        f"{{{DAV}}}getcontenttype": _xml_element(f"{{{DAV}}}getcontenttype", mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
        f"{{{DAV}}}getlastmodified": _xml_element(f"{{{DAV}}}getlastmodified", formatdate(stat.st_mtime, usegmt=True)),
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
) -> str:
    active_lock = _lock_for(_lock_key(resource, document)) if resource is not None else None
    live = _live_properties(
        display_name,
        collection=collection,
        document=document,
        sync_token=sync_token,
        quota=_quota_state() if collection and username else None,
        lock=active_lock,
        href=href,
    )
    dead = _dead_properties(username, resource, document) if username and resource is not None else {}
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
            f"{{{DAV}}}quota-available-bytes", f"{{{DAV}}}quota-used-bytes",
            f"{{{DAV}}}sync-token", f"{{{DAV}}}supported-report-set",
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
    return f'<d:response><d:href>{escape(href)}</d:href>{propstats}</d:response>'


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
    if tag not in MUTABLE_DAV_PROPERTIES:
        return True
    element = ElementTree.fromstring(serialized)
    if list(element):
        return False
    text = element.text or ""
    if tag == f"{{{DAV}}}displayname":
        return len(text.encode("utf-8")) <= 1024 and "\x00" not in text
    return bool(re.fullmatch(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", text.strip()))


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


def _copy_dead_properties(username: str, source: Path, source_document: dict, destination: Path, destination_document: dict) -> None:
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
                tokens.append({"token": token, "revision": revision})
                state["token"] = token
            state["revision"] = revision
            state["snapshot"] = current
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
            token = _new_sync_token()
            state = {
                "revision": 0,
                "token": token,
                "tokens": [{"token": token, "revision": 0}],
                "changes": [],
                "snapshot": snapshot,
            }
            collections[collection_key] = state
        else:
            previous = state.get("snapshot", {}) if isinstance(state.get("snapshot"), dict) else {}
            changes = state.get("changes", []) if isinstance(state.get("changes"), list) else []
            tokens = state.get("tokens", []) if isinstance(state.get("tokens"), list) else []
            revision = int(state.get("revision", 0))
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
                tokens.append({"token": token, "revision": revision})
                state["token"] = token
            state["revision"] = revision
            state["snapshot"] = snapshot
            state["changes"] = changes[-MAX_SYNC_CHANGES:]
            minimum = state["changes"][0]["revision"] - 1 if state["changes"] else revision
            retained = [item for item in tokens if int(item.get("revision", -1)) >= minimum]
            state["tokens"] = retained[-MAX_SYNC_TOKENS:]
            if not state.get("token"):
                state["token"] = state["tokens"][-1]["token"] if state["tokens"] else _new_sync_token()
        payload["version"] = 1
        atomic_json_write(path, payload)
        return json.loads(json.dumps(state))


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


def _sync_if_error(username: str) -> Response | None:
    """Validate RFC 6578 collection tokens used as tagged If state tokens."""
    value = request.headers.get("If", "")
    if "urn:uuid:" not in value:
        return None
    pairs = re.findall(r"<([^>]+)>\s*\(\s*<(urn:uuid:[^>]+)>", value, re.I)
    if not pairs:
        untagged = re.findall(r"\(\s*<(urn:uuid:[^>]+)>", value, re.I)
        if len(untagged) != 1:
            return Response("invalid collection sync-token precondition", 412)
        pairs = [(request.path, untagged[0])]
    prefix = f"/webdav/files/{quote(username, safe='')}"
    for resource_tag, supplied_token in pairs:
        parsed = urlsplit(resource_tag)
        if parsed.netloc and parsed.netloc.casefold() != request.host.casefold():
            return Response("collection sync-token belongs to another server", 412)
        tagged_path = unquote(parsed.path)
        if tagged_path.rstrip("/") == prefix:
            relative = ""
        elif tagged_path.startswith(prefix + "/"):
            relative = tagged_path[len(prefix):].strip("/")
        else:
            return Response("collection sync-token belongs to another user tree", 412)
        try:
            collection = _tree_path(relative)
        except ValueError:
            return Response("collection sync-token target is invalid", 412)
        if not collection.is_dir() or collection.is_symlink():
            return Response("collection sync-token target is not a collection", 412)
        current_token = str(_sync_state(username, collection)["token"])
        if not hmac.compare_digest(supplied_token, current_token):
            return Response("collection changed since synchronization", 412)
    return None


def _sync_member_in_scope(relative: str, collection: Path, level: str) -> bool:
    collection_relative = _store().relative(collection)
    try:
        nested = Path(relative).relative_to(collection_relative) if collection_relative else Path(relative)
    except ValueError:
        return False
    return nested != Path(".") and (level == "infinite" or len(nested.parts) == 1)


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
    if root.find(f"{{{DAV}}}limit") is not None:
        error = '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:"><d:number-of-matches-within-limits/></d:error>'
        return Response(error, 507, mimetype="application/xml")

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
        members = [latest[key] for key in sorted(latest)]
    else:
        members = [
            {"path": relative, "removed": False, "collection": bool(info.get("collection"))}
            for relative, info in sorted(state.get("snapshot", {}).items())
            if _sync_member_in_scope(relative, collection, level)
        ]

    removed_collections = {
        str(item["path"]) for item in members
        if item.get("removed") and item.get("collection")
    }
    responses: list[str] = []
    for item in members:
        relative = str(item["path"])
        if level == "infinite" and any(relative.startswith(parent + "/") for parent in removed_collections):
            continue
        href = _tree_url(username, relative, collection=bool(item.get("collection")))
        if item.get("removed"):
            responses.append(f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>")
            continue
        resource = _store().root / relative
        if item.get("collection"):
            responses.append(_prop_response(href, resource.name, collection=True, username=username, resource=resource, query=query))
        else:
            try:
                document = _tree_document(resource)
            except ValueError:
                continue
            responses.append(_prop_response(href, resource.name, document=document, username=username, resource=resource, query=query))
    xml = f'''<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}<d:sync-token>{escape(str(state["token"]))}</d:sync-token></d:multistatus>'''
    return Response(xml, 207, mimetype="application/xml")


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
                )
            except (TypeError, ValueError) as exc:
                flash(str(exc) or "WebDAV-Zugang konnte nicht angelegt werden.")
    credentials = credentials_for(username)
    configured = any(not item["expired"] for item in credentials)
    webdav_url = _resource_url(username, document, external=True)
    webdav_root_url = _tree_url(username, external=True, collection=True)
    return render_template(
        "documents/libreoffice.html",
        document=document,
        webdav_url=webdav_url,
        webdav_root_url=webdav_root_url,
        configured=configured,
        generated_password=generated_password,
        credentials=credentials,
        quota=_quota_state(),
    )


@bp.route("/webdav/files/<username>", defaults={"relative_path": ""}, methods=["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
@bp.route("/webdav/files/<username>/<path:relative_path>", methods=["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"])
def file_tree(username: str, relative_path: str):
    """Hierarchical WebDAV namespace for desktop file managers and sync clients."""
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    if identity["username"] != username:
        return Response("not found", 404)
    allow = "OPTIONS, PROPFIND, REPORT, GET, HEAD" if identity["scope"] == "read" else "OPTIONS, PROPFIND, PROPPATCH, REPORT, GET, HEAD, PUT, DELETE, MKCOL, COPY, MOVE, LOCK, UNLOCK"
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1, 2, sync-collection", "MS-Author-Via": "DAV", "Allow": allow,
            "Want-Content-Digest": DIGEST_PREFERENCE,
        })
    if identity["scope"] != "write" and request.method in WRITE_METHODS:
        return Response("this WebDAV credential is read-only", 403, {"Allow": allow})
    try:
        resource = _tree_path(relative_path)
    except ValueError:
        return Response("not found", 404)
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
        sync_error = _sync_if_error(username)
        if sync_error is not None:
            return sync_error

    if request.method == "REPORT":
        if not is_collection:
            return Response("sync-collection requires a collection", 400)
        return _sync_report(username, resource)

    if request.method == "PROPFIND":
        if not is_collection and document is None:
            return Response("not found", 404)
        depth = request.headers.get("Depth", "0")
        if depth not in {"0", "1"}:
            return Response("finite Depth required", 403)
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
        href = _tree_url(username, _store().relative(resource), collection=is_collection)
        wants_sync_token = query[0] == "propname" or f"{{{DAV}}}sync-token" in query[1]
        sync_token = _collection_sync_token(username, resource) if is_collection and wants_sync_token else ""
        responses = [_prop_response(href, resource.name if resource != _store().root else "SimpleOffice Dokumente", collection=is_collection, document=document, sync_token=sync_token, username=username, resource=resource, query=query)]
        if is_collection and depth == "1":
            for child in sorted(resource.iterdir(), key=lambda item: item.name.casefold()):
                if child.name in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE} or child.is_symlink():
                    continue
                child_href = _tree_url(username, _store().relative(child), collection=child.is_dir())
                if child.is_dir():
                    responses.append(_prop_response(child_href, child.name, collection=True, username=username, resource=child, query=query))
                elif child.is_file():
                    try:
                        child_document = _tree_document(child)
                    except ValueError:
                        continue
                    responses.append(_prop_response(child_href, child.name, document=child_document, username=username, resource=child, query=query))
        return Response(f'''<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}</d:multistatus>''', 207, mimetype="application/xml")

    if request.method in {"GET", "HEAD"}:
        if document is None:
            return Response("not found", 404)
        return _download_response(
            resource, username, document,
            mimetypes.guess_type(resource.name)[0] or "application/octet-stream",
        )

    key = _lock_key(resource, document)
    lock_error = _require_lock(key, username)
    if lock_error is not None and request.method not in {"LOCK", "UNLOCK"}:
        return lock_error

    if request.method == "PROPPATCH":
        if not is_collection and document is None:
            return Response("not found", 404)
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
        try:
            _store().create_collection(_store().relative(resource), f"webdav:{username}")
        except ValueError:
            return Response("parent collection does not exist", 409)
        _record_sync_changes(username, _store().relative(resource))
        return Response("", 201)

    if request.method == "LOCK":
        return _lock_request(username, resource, document, request.url)

    if request.method == "UNLOCK":
        token = _request_token()
        existing = _lock_for(key)
        if not existing or existing.get("token") != token or existing.get("username") != username:
            return Response("lock token does not match", 409)
        _release_lock(key)
        _record_lock_audit("webdav_lock_released", username, resource, existing)
        return Response("", 204)

    if request.method == "PUT":
        content = request.get_data()
        current_etag = _etag(document) if document else ""
        if document:
            if request.headers.get("If-None-Match") == "*":
                return Response("resource already exists", 412, {"ETag": current_etag})
            if_match = request.headers.get("If-Match", "")
            if not _request_token() and not if_match:
                return Response("existing resources require If-Match or a lock token", 428, {"ETag": current_etag})
            if if_match and if_match != "*" and _etag_value(if_match) != _etag_value(current_etag):
                return Response("resource changed since it was opened", 412, {"ETag": current_etag})
            digest_error = _verify_content_digest(content, username, resource)
            if digest_error is not None:
                return digest_error
            quota_error = _check_quota(username, "PUT", resource, len(content) - resource.stat().st_size)
            if quota_error is not None:
                return quota_error
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
        if request.headers.get("If-Match"):
            return Response("resource does not exist", 412)
        digest_error = _verify_content_digest(content, username, resource)
        if digest_error is not None:
            return digest_error
        quota_error = _check_quota(username, "PUT", resource, len(content))
        if quota_error is not None:
            return quota_error
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
            )
        _record_sync_changes(username, _store().relative(resource))
        return Response("", 201, _stored_integrity_headers(created))

    if request.method == "DELETE":
        if document:
            if_match = request.headers.get("If-Match", "")
            current_etag = _etag(document)
            if if_match and if_match != "*" and _etag_value(if_match) != _etag_value(current_etag):
                return Response("resource changed since it was opened", 412, {"ETag": current_etag})
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
            try:
                _store().delete_empty_collection(_store().relative(resource), f"webdav:{username}")
            except ValueError as exc:
                return Response(str(exc), 409)
            _record_sync_changes(username, _store().relative(resource))
            return Response("", 204)
        return Response("not found", 404)

    if request.method in {"COPY", "MOVE"}:
        if document is None:
            return Response("only regular files can be copied or moved", 501 if is_collection else 404)
        current_etag = _etag(document)
        if_match = request.headers.get("If-Match", "")
        if if_match and if_match != "*" and _etag_value(if_match) != _etag_value(current_etag):
            return Response("resource changed since it was opened", 412, {"ETag": current_etag})
        try:
            destination, destination_relative = _destination(username)
        except PermissionError:
            return Response("destination is outside the authenticated WebDAV tree", 502)
        except ValueError as exc:
            return Response(str(exc), 400)
        if destination.exists():
            return Response("destination exists; explicit replacement is required", 412)
        if not destination.parent.is_dir():
            return Response("destination parent does not exist", 409)
        destination_lock = _require_lock(_lock_key(destination), username)
        if destination_lock is not None:
            return destination_lock
        if request.method == "COPY":
            quota_error = _check_quota(username, "COPY", destination, resource.stat().st_size)
            if quota_error is not None:
                return quota_error
        try:
            if request.method == "COPY":
                result = _store().copy_document(document["document_id"], destination_relative, f"webdav:{username}")
                _copy_dead_properties(username, resource, document, destination, result)
            else:
                with exclusive_file_lock(_store().control / ".document-content.lock"):
                    result = _store().move_document(document["document_id"], _store().relative(destination.parent), f"webdav:{username}", destination_name=destination.name)
        except (FileExistsError, ValueError) as exc:
            status = 423 if "locked" in str(exc) or "staged" in str(exc) else 409
            return Response(str(exc), status)
        if request.method == "COPY":
            _record_sync_changes(username, destination_relative)
        else:
            _record_sync_changes(username, _store().relative(resource), destination_relative)
        return Response("", 201, {"ETag": _etag(result), "Location": _tree_url(username, result["last_path"])})

    return Response("method not allowed", 405, {"Allow": allow})


@bp.route("/webdav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND"])
@bp.route("/webdav/<path:path>", methods=["OPTIONS", "PROPFIND", "PROPPATCH", "GET", "HEAD", "PUT", "LOCK", "UNLOCK"])
def endpoint(path: str):
    identity = _authenticate()
    if identity is None:
        return _unauthorized()
    username = identity["username"]
    allow = "OPTIONS, PROPFIND, GET, HEAD" if identity["scope"] == "read" else "OPTIONS, PROPFIND, PROPPATCH, GET, HEAD, PUT, LOCK, UNLOCK"
    if request.method == "OPTIONS":
        return Response("", 204, {
            "DAV": "1, 2", "MS-Author-Via": "DAV", "Allow": allow,
            "Want-Content-Digest": DIGEST_PREFERENCE,
        })
    if identity["scope"] != "write" and request.method in WRITE_METHODS:
        return Response("this WebDAV credential is read-only", 403, {"Allow": allow})

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
                    responses.append(_prop_response(_resource_url(username, item), Path(item["last_path"]).name, document=item, username=username, resource=item_path, query=query))
        elif document is not None:
            responses.append(_prop_response(request.path, document_path.name, document=document, username=username, resource=document_path, query=query))
        else:
            return Response("not found", 404)
        return Response(f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}</d:multistatus>', 207, mimetype="application/xml")

    if document is None:
        return Response("not found", 404)
    if request.method == "PROPPATCH":
        mutation_lock = exclusive_file_lock(_sync_path().with_suffix(".mutation.lock"))
        mutation_lock.__enter__()
        g._webdav_mutation_lock = mutation_lock
        lock_error = _require_lock(document["document_id"], username)
        if lock_error is not None:
            return lock_error
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
        token = _request_token()
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
        content = request.get_data()
        token = _request_token()
        lock = _active_locks().get("locks", {}).get(document["document_id"])
        if lock and (lock.get("token") != token or lock.get("username") != username):
            return Response("document is locked", 423, common_headers)
        if_match = request.headers.get("If-Match", "")
        if if_match and if_match != "*" and _etag_value(if_match) != _etag_value(current_etag):
            return Response("document changed since it was opened", 412, common_headers)
        digest_error = _verify_content_digest(content, username, document_path)
        if digest_error is not None:
            return digest_error
        quota_error = _check_quota(username, "PUT", document_path, len(content) - document_path.stat().st_size)
        if quota_error is not None:
            return quota_error
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
