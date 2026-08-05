"""Writable, versioned WebDAV endpoint for LibreOffice remote editing."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, sha256_file, utc_now
from .file_lock import exclusive_file_lock


bp = Blueprint("webdav", __name__)
DAV = "DAV:"


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _credentials_path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "webdav-credentials.json"


def _locks_path() -> Path:
    return Path(current_app.config["DOCUMENT_ROOT"]) / CONTROL_DIR / "webdav-locks.json"


def _read_json(path: Path, fallback: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def activate(username: str, actor: str) -> str:
    """Replace a user's WebDAV app password and return it exactly once."""
    password = secrets.token_urlsafe(24)
    salt = os.urandom(16)
    record = {
        "salt": salt.hex(),
        "hash": hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex(),
        "created_at": utc_now(),
        "created_by": actor,
    }
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"users": {}})
        payload.setdefault("users", {})[username] = record
        atomic_json_write(path, payload)
    _store().history.record("webdav_credentials_rotated", actor, "webdav", hashlib.sha256(username.encode()).hexdigest()[:16], {"username": username, "created_at": record["created_at"]})
    return password


def revoke(username: str, actor: str) -> None:
    path = _credentials_path()
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload = _read_json(path, {"users": {}})
        payload.setdefault("users", {}).pop(username, None)
        atomic_json_write(path, payload)
    _store().history.record("webdav_credentials_revoked", actor, "webdav", hashlib.sha256(username.encode()).hexdigest()[:16], {"username": username, "revoked_at": utc_now()})


def _authenticate() -> str | None:
    supplied = request.authorization
    if not supplied or supplied.type.casefold() != "basic" or not supplied.username or not supplied.password:
        return None
    record = _read_json(_credentials_path(), {"users": {}}).get("users", {}).get(supplied.username)
    if not isinstance(record, dict):
        return None
    try:
        actual = hashlib.scrypt(supplied.password.encode(), salt=bytes.fromhex(record["salt"]), n=2**14, r=8, p=1)
        expected = bytes.fromhex(record["hash"])
    except (KeyError, ValueError):
        return None
    return supplied.username if hmac.compare_digest(actual, expected) else None


def _unauthorized() -> Response:
    return Response("WebDAV authentication required", 401, {"WWW-Authenticate": 'Basic realm="SimpleOffice4Me Documents"'})


def _document_path(document: dict) -> Path:
    path = _store().root / str(document.get("last_path", ""))
    if not path.is_file() or path.is_symlink():
        raise ValueError("document unavailable")
    return path


def _etag(document: dict) -> str:
    path = _document_path(document)
    return f'"{sha256_file(path)}"'


def _etag_value(value: str) -> str:
    return value.strip().removeprefix("W/").strip('"')


def _resource_url(username: str, document: dict, *, external: bool = False) -> str:
    filename = Path(str(document.get("last_path", "document"))).name
    return url_for("webdav.endpoint", path=f"documents/{username}/{document['document_id']}--{filename}", _external=external)


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


def _save_lock(document_id: str, username: str, token: str, timeout_seconds: int, owner: str = "") -> dict:
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
            "created_at": existing.get("created_at", utc_now()) if existing else utc_now(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat(),
        }
        payload["locks"][document_id] = lock
        atomic_json_write(path, payload)
        return lock


def _timeout_seconds() -> int:
    match = re.search(r"Second-(\d+)", request.headers.get("Timeout", ""), re.I)
    return max(60, min(int(match.group(1)) if match else 1800, 3600))


def _lock_xml(lock: dict, href: str) -> str:
    seconds = max(0, int((datetime.fromisoformat(lock["expires_at"]) - datetime.now(timezone.utc)).total_seconds()))
    return f'''<?xml version="1.0" encoding="utf-8"?><d:prop xmlns:d="DAV:"><d:lockdiscovery><d:activelock><d:locktype><d:write/></d:locktype><d:lockscope><d:exclusive/></d:lockscope><d:depth>0</d:depth><d:owner>{escape(lock.get("owner", ""))}</d:owner><d:timeout>Second-{seconds}</d:timeout><d:locktoken><d:href>{escape(lock["token"])}</d:href></d:locktoken><d:lockroot><d:href>{escape(href)}</d:href></d:lockroot></d:activelock></d:lockdiscovery></d:prop>'''


def _prop_response(href: str, display_name: str, *, collection: bool = False, document: dict | None = None) -> str:
    if collection:
        properties = f"<d:resourcetype><d:collection/></d:resourcetype><d:displayname>{escape(display_name)}</d:displayname>"
    else:
        path = _document_path(document or {})
        properties = f"<d:resourcetype/><d:displayname>{escape(display_name)}</d:displayname><d:getcontentlength>{path.stat().st_size}</d:getcontentlength><d:getcontenttype>application/octet-stream</d:getcontenttype><d:getetag>{escape(_etag(document or {}))}</d:getetag>"
    return f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{properties}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"


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
        if action == "revoke":
            revoke(username, username)
            flash("LibreOffice-WebDAV wurde deaktiviert.")
        else:
            generated_password = activate(username, username)
    configured = username in _read_json(_credentials_path(), {"users": {}}).get("users", {})
    webdav_url = _resource_url(username, document, external=True)
    return render_template("documents/libreoffice.html", document=document, webdav_url=webdav_url, configured=configured, generated_password=generated_password)


@bp.route("/webdav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND"])
@bp.route("/webdav/<path:path>", methods=["OPTIONS", "PROPFIND", "GET", "HEAD", "PUT", "LOCK", "UNLOCK"])
def endpoint(path: str):
    username = _authenticate()
    if username is None:
        return _unauthorized()
    if request.method == "OPTIONS":
        return Response("", 204, {"DAV": "1, 2", "MS-Author-Via": "DAV", "Allow": "OPTIONS, PROPFIND, GET, HEAD, PUT, LOCK, UNLOCK"})

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
        responses: list[str] = []
        if not parts:
            responses.append(_prop_response(request.path, "SimpleOffice4Me", collection=True))
        elif parts == ["documents", username]:
            responses.append(_prop_response(request.path, "SimpleOffice Dokumente", collection=True))
            if depth == "1":
                for item in _store().list_documents():
                    try:
                        _document_path(item)
                    except ValueError:
                        continue
                    responses.append(_prop_response(_resource_url(username, item), Path(item["last_path"]).name, document=item))
        elif document is not None:
            responses.append(_prop_response(request.path, document_path.name, document=document))
        else:
            return Response("not found", 404)
        return Response(f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">{"".join(responses)}</d:multistatus>', 207, mimetype="application/xml")

    if document is None:
        return Response("not found", 404)
    current_etag = _etag(document)
    common_headers = {"ETag": current_etag, "Accept-Ranges": "bytes", "Cache-Control": "private, no-cache"}
    if request.method in {"GET", "HEAD"}:
        data = document_path.read_bytes()
        headers = {**common_headers, "Content-Type": "application/octet-stream", "Content-Length": str(len(data))}
        response = Response(data if request.method == "GET" else None, 200)
        response.headers.update(headers)
        return response
    if request.method == "LOCK":
        token = _request_token() or f"opaquelocktoken:{uuid.uuid4()}"
        owner = ""
        try:
            root = ElementTree.fromstring(request.get_data() or b"<lockinfo xmlns='DAV:'/>")
            owner_node = root.find("{DAV:}owner")
            owner = "".join(owner_node.itertext()) if owner_node is not None else ""
        except ElementTree.ParseError:
            return Response("invalid LOCK body", 400)
        try:
            lock = _save_lock(document["document_id"], username, token, _timeout_seconds(), owner)
        except PermissionError:
            return Response("locked", 423)
        body = _lock_xml(lock, request.url)
        return Response(body, 200, {"Content-Type": "application/xml; charset=utf-8", "Lock-Token": f"<{token}>", **common_headers})
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
        return Response("", 204)
    if request.method == "PUT":
        token = _request_token()
        lock = _active_locks().get("locks", {}).get(document["document_id"])
        if lock and (lock.get("token") != token or lock.get("username") != username):
            return Response("document is locked", 423, common_headers)
        if_match = request.headers.get("If-Match", "")
        if if_match and if_match != "*" and _etag_value(if_match) != _etag_value(current_etag):
            return Response("document changed since it was opened", 412, common_headers)
        try:
            updated = _store().replace_content(
                document["document_id"], request.get_data(), f"webdav:{username}",
                expected_sha256=_etag_value(current_etag), max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
            )
        except ValueError as exc:
            message = str(exc)
            status = 412 if "changed since" in message else 423 if "locked" in message or "staged" in message else 400
            return Response(str(exc), status, common_headers)
        return Response("", 204, {"ETag": _etag(updated), "Cache-Control": "private, no-cache"})
    return Response("method not allowed", 405)
