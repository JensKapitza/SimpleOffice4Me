"""S3 path-style protocol endpoints and credential administration."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.access_control import audit, has_feature, is_admin
from app.auth import login_required
from app.db import get_db

from . import auth, credentials
from .objects import DocumentObjects
from .xml import document as xml_document, element, error as xml_error


bp = Blueprint("s3_overlay", __name__)
S3_PREFIX = "/s3"
BUCKET = "simpleoffice"


class S3Error(Exception):
    def __init__(self, code: str, message: str, status: int, headers: dict[str, str] | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.headers = headers or {}


def _enabled() -> bool:
    if not current_app.config.get("S3_OVERLAY_ENABLED", False):
        raise S3Error("NotFound", "S3 overlay is disabled", 404)
    host = request.host.split(":", 1)[0].strip("[]").casefold()
    if not request.is_secure and host not in {"localhost", "127.0.0.1", "::1"}:
        raise S3Error("AccessDenied", "HTTPS is required for S3 access", 403)
    return True


def _authenticate(required_scope: str | None = None) -> tuple[dict, DocumentObjects]:
    _enabled()
    try:
        identity = auth.authenticate()
    except (auth.SignatureError, ValueError, OverflowError) as exc:
        raise S3Error("SignatureDoesNotMatch", str(exc), 403) from exc
    row = get_db().execute("SELECT * FROM user WHERE username=?", (identity["username"],)).fetchone()
    if row is None or row["is_disabled"]:
        raise S3Error("AccessDenied", "S3 access is not available", 403)
    if not has_feature(row, "documents"):
        raise S3Error("AccessDenied", "S3 access is not available", 403)
    if required_scope and required_scope not in identity["scopes"]:
        raise S3Error("AccessDenied", "The S3 credential lacks the required scope", 403)
    key_prefix = identity.get("prefix", "")
    if key_prefix and request.view_args and request.view_args.get("key"):
        if not request.view_args["key"].startswith(key_prefix):
            raise S3Error("NoSuchKey", "The specified key does not exist", 404)
    return identity, DocumentObjects(identity["username"])


def _response(payload: str | bytes = b"", status: int = 200, content_type: str = "application/xml; charset=utf-8") -> Response:
    response = Response(payload, status=status, content_type=content_type)
    response.headers["x-amz-request-id"] = getattr(g, "request_id", uuid.uuid4().hex)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.errorhandler(S3Error)
def _s3_error(exc: S3Error):
    response = _response(xml_error(exc.code, exc.message, getattr(g, "request_id", "unknown")), exc.status)
    response.headers.update(exc.headers)
    return response


@bp.errorhandler(RequestEntityTooLarge)
def _too_large(_exc):
    return _s3_error(S3Error("EntityTooLarge", "The upload exceeds the configured limit", 413))


@bp.route("/s3", methods=["GET"])
@bp.route("/s3/", methods=["GET"])
def list_buckets():
    identity, _provider = _authenticate()
    scopes = identity["scopes"]
    if not ({"read", "inbox:put"} & set(scopes)):
        raise S3Error("AccessDenied", "The S3 credential lacks access", 403)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    xml = xml_document("ListAllMyBucketsResult", element("Owner", element("ID", identity["username"]) + element("DisplayName", identity["username"])) +
                       f"<Buckets><Bucket>{element('Name', BUCKET)}{element('CreationDate', created)}</Bucket></Buckets>")
    return _response(xml)


@bp.route("/s3/<bucket>", defaults={"key": ""}, methods=["GET", "HEAD", "PUT", "POST", "DELETE"])
@bp.route("/s3/<bucket>/<path:key>", methods=["GET", "HEAD", "PUT", "POST", "DELETE"])
def bucket_object(bucket: str, key: str):
    method = request.method
    identity, provider = _authenticate("inbox:put" if method == "PUT" else "read")
    if bucket != BUCKET:
        raise S3Error("NoSuchBucket", "The specified bucket does not exist", 404)
    if not key:
        if method == "HEAD":
            return _response(b"", 200)
        if method == "GET" and "location" in request.args:
            region = current_app.config.get("S3_OVERLAY_REGION", "us-east-1")
            return _response(xml_document("LocationConstraint", escape(region)))
        if method == "GET" and "versioning" in request.args:
            return _response(xml_document("VersioningConfiguration", ""))
        if method == "GET":
            return _list_objects(provider, identity)
        raise S3Error("MethodNotAllowed", "The requested bucket operation is not supported", 405)
    if method == "PUT":
        return _put_inbox(provider, identity, key)
    if method in {"POST", "DELETE"}:
        raise S3Error("AccessDenied", "This S3 mutation is not supported", 403)
    return _get_object(provider, identity, key, head=method == "HEAD")


def _cursor_decode(token: str) -> str:
    try:
        payload_text, mac_text = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4))
        expected = hmac.new(bytes(current_app.config["SECRET_KEY"], "utf-8") if isinstance(current_app.config["SECRET_KEY"], str) else current_app.config["SECRET_KEY"], payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, mac_text):
            raise ValueError("bad token")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict) or value.get("v") != 1 or not isinstance(value.get("key"), str):
            raise ValueError("bad token")
        return value["key"]
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise S3Error("InvalidArgument", "The continuation token is invalid", 400) from exc


def _cursor_encode(key: str) -> str:
    payload = json.dumps({"v": 1, "key": key}, separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    mac = hmac.new(bytes(current_app.config["SECRET_KEY"], "utf-8") if isinstance(current_app.config["SECRET_KEY"], str) else current_app.config["SECRET_KEY"], payload, hashlib.sha256).hexdigest()
    return token + "." + mac


def _list_objects(provider: DocumentObjects, identity: dict):
    list_type = request.args.get("list-type")
    if list_type not in {None, "2"}:
        raise S3Error("InvalidArgument", "The requested ListObjects version is not supported", 400)
    legacy = list_type is None
    try:
        count = int(request.args.get("max-keys", "1000"))
    except ValueError as exc:
        raise S3Error("InvalidArgument", "max-keys must be an integer", 400) from exc
    if not 0 <= count <= 1000:
        raise S3Error("InvalidArgument", "max-keys must be between 0 and 1000", 400)
    prefix = request.args.get("prefix", "")
    access_prefix = identity.get("prefix", "")
    if access_prefix and not prefix.startswith(access_prefix):
        prefix = access_prefix
    if legacy:
        after = request.args.get("marker", "")
    else:
        after = _cursor_decode(request.args["continuation-token"]) if request.args.get("continuation-token") else request.args.get("start-after", "")
    delimiter = request.args.get("delimiter", "")
    if delimiter and len(delimiter) > 1:
        raise S3Error("InvalidArgument", "delimiter must be one character", 400)
    entries = []
    seen_common = set()
    seen_entries = set()
    last_key = after
    scan_after = after
    truncated = False
    batch_size = max(100, min(1000, count + 1))
    while True:
        rows = provider.keys(prefix=prefix, after=scan_after, limit=batch_size)
        if not rows:
            break
        for obj in rows:
            relative = obj.key[len(prefix):] if obj.key.startswith(prefix) else obj.key
            common_prefix = prefix + relative.split(delimiter, 1)[0] + delimiter if delimiter and delimiter in relative else None
            entry = ("prefix", common_prefix) if common_prefix is not None else ("object", obj.key)
            if entry not in seen_entries and (common_prefix is None or common_prefix not in seen_common):
                if len(entries) >= count:
                    truncated = True
                    break
                seen_entries.add(entry)
                if common_prefix is not None:
                    seen_common.add(common_prefix)
                entries.append((entry[0], common_prefix if common_prefix is not None else obj))
            last_key = obj.key
        if truncated or len(rows) <= batch_size:
            break
        scan_after = last_key
    encoding = request.args.get("encoding-type") == "url"
    def key_text(value: str) -> str:
        return quote(value, safe="/-_.~") if encoding else value
    parts = [element("Name", BUCKET), element("Prefix", key_text(prefix)), element("KeyCount", len(entries)), element("MaxKeys", count), element("IsTruncated", str(truncated).lower())]
    if legacy:
        parts.append(element("Marker", key_text(request.args.get("marker", ""))))
    elif request.args.get("continuation-token"):
        parts.append(element("ContinuationToken", request.args["continuation-token"]))
    if truncated and last_key:
        if legacy:
            parts.append(element("NextMarker", key_text(last_key)))
        else:
            token = _cursor_encode(last_key)
            parts.append(element("NextContinuationToken", token))
    for kind, value in entries:
        if kind == "prefix":
            parts.append("<CommonPrefixes>" + element("Prefix", key_text(value)) + "</CommonPrefixes>")
        else:
            obj = value
            parts.append("<Contents>" + element("Key", key_text(obj.key)) + element("LastModified", obj.modified.strftime("%Y-%m-%dT%H:%M:%S.000Z")) + element("ETag", f'"{obj.etag}"') + element("Size", obj.size) + element("StorageClass", "STANDARD") + "</Contents>")
    parts.append(element("EncodingType", "url") if encoding else "")
    return _response(xml_document("ListBucketResult", "".join(parts)))


def _get_object(provider: DocumentObjects, identity: dict, key: str, *, head: bool):
    key = key.lstrip("/")
    if key.startswith("_meta/") or key.startswith("documents/"):
        if "read" not in identity["scopes"]:
            raise S3Error("AccessDenied", "The S3 credential lacks read access", 403)
        obj = provider.resolve(key)
        if obj is None:
            raise S3Error("NoSuchKey", "The specified key does not exist", 404)
        return _serve_object(provider, obj, head=head)
    elif key.startswith("inbox/"):
        if "read" not in identity["scopes"]:
            raise S3Error("NoSuchKey", "The specified key does not exist", 404)
        obj = provider.resolve(key)
        if obj is None:
            raise S3Error("NoSuchKey", "The specified key does not exist", 404)
        return _serve_object(provider, obj, head=head)
    else:
        raise S3Error("NoSuchKey", "The specified key does not exist", 404)
    obj = provider.resolve(key)
    if obj is None:
        raise S3Error("NoSuchKey", "The specified key does not exist", 404)
    return _serve_object(provider, obj, head=head)


def _http_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _etag_matches(header: str, tag: str, *, weak: bool = False) -> bool:
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if weak:
            candidate = candidate[2:] if candidate.startswith("W/") else candidate
            comparison = tag[2:] if tag.startswith("W/") else tag
        else:
            comparison = tag
        if candidate == comparison:
            return True
    return False


def _range_error(message: str, size: int) -> S3Error:
    return S3Error("InvalidRange", message, 416, {"Content-Range": f"bytes */{size}"})


def _serve_object(provider: DocumentObjects, obj, *, head: bool):
    tag = f'"{obj.etag}"'
    if_match = request.headers.get("If-Match", "")
    if if_match and not _etag_matches(if_match, tag):
        raise S3Error("PreconditionFailed", "If-Match condition failed", 412)
    if_unmodified = _http_date(request.headers.get("If-Unmodified-Since", ""))
    if not if_match and if_unmodified and obj.modified.replace(microsecond=0) > if_unmodified:
        raise S3Error("PreconditionFailed", "If-Unmodified-Since condition failed", 412)
    if_none_match = request.headers.get("If-None-Match", "")
    if if_none_match and _etag_matches(if_none_match, tag, weak=True):
        response = _response(b"", 304)
        response.headers.update({"ETag": tag, "Last-Modified": obj.modified.strftime("%a, %d %b %Y %H:%M:%S GMT")})
        return response
    if_modified = _http_date(request.headers.get("If-Modified-Since", ""))
    if not if_none_match and if_modified and obj.modified.replace(microsecond=0) <= if_modified:
        response = _response(b"", 304)
        response.headers.update({"ETag": tag, "Last-Modified": obj.modified.strftime("%a, %d %b %Y %H:%M:%S GMT")})
        return response
    status = 200
    start = 0
    end = obj.size - 1
    range_header = request.headers.get("Range", "")
    if not head and range_header:
        if not range_header.startswith("bytes=") or "," in range_header:
            raise _range_error("Only one byte range is supported", obj.size)
        start_text, sep, end_text = range_header[6:].partition("-")
        if not sep:
            raise _range_error("The byte range is invalid", obj.size)
        try:
            if not start_text:
                suffix = int(end_text)
                if suffix <= 0:
                    raise ValueError("suffix must be positive")
                start = max(0, obj.size - suffix)
                end = obj.size - 1
            else:
                start = int(start_text)
                end = min(int(end_text), obj.size - 1) if end_text else obj.size - 1
        except ValueError as exc:
            raise _range_error("The byte range is invalid", obj.size) from exc
        if start < 0 or end < start or start >= obj.size:
            raise _range_error("The requested range is not satisfiable", obj.size)
        status = 206
    selected_length = 0 if head else (end - start + 1 if status == 206 else obj.size)
    if head:
        response = _response(b"", status, obj.content_type)
    else:
        spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        try:
            copied = provider.read_to(obj, spool, start=start, length=selected_length)
            if copied != selected_length:
                raise OSError("verified object range length mismatch")
            spool.seek(0)
        except (OSError, RuntimeError, ValueError) as exc:
            spool.close()
            raise S3Error("InternalError", "Object integrity or storage verification failed", 500) from exc
        def stream():
            try:
                while block := spool.read(64 * 1024):
                    yield block
            finally:
                spool.close()
        response = _response(stream(), status, obj.content_type)
    response.headers.update({"ETag": tag, "Last-Modified": obj.modified.strftime("%a, %d %b %Y %H:%M:%S GMT"), "Accept-Ranges": "bytes", "Content-Length": str(selected_length if not head else obj.size)})
    if status == 206:
        response.headers["Content-Range"] = f"bytes {start}-{end}/{obj.size}"
    return response


def _put_inbox(provider: DocumentObjects, identity: dict, key: str):
    if not key.startswith("inbox/") or len(key) <= len("inbox/"):
        raise S3Error("AccessDenied", "PUT is allowed only for inbox objects", 403)
    expected_hash = request.headers.get("X-Amz-Content-Sha256", "")
    if not expected_hash or not all(ch in "0123456789abcdefABCDEF" for ch in expected_hash) or len(expected_hash) != 64:
        raise S3Error("InvalidRequest", "A SHA-256 signed payload is required", 400)
    limit = int(current_app.config.get("S3_OVERLAY_MAX_UPLOAD_BYTES", current_app.config.get("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)))
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            block = request.stream.read(min(1024 * 1024, limit - total + 1))
            if not block:
                break
            total += len(block)
            if total > limit:
                raise S3Error("EntityTooLarge", "The upload exceeds the configured limit", 413)
            digest.update(block)
            spool.write(block)
        if digest.hexdigest() != expected_hash.casefold():
            raise S3Error("BadDigest", "The request body does not match its SHA-256 header", 400)
        spool.seek(0)
        try:
            doc = provider.put_inbox(key[len("inbox/"):], spool, limit, digest.hexdigest())
        except FileExistsError as exc:
            raise S3Error("PreconditionFailed", "The inbox key already exists", 412) from exc
        except ValueError as exc:
            raise S3Error("InvalidArgument", "The inbox key or file could not be imported", 400) from exc
        response = _response(b"", 200)
        response.headers["ETag"] = f'"{doc.get("sha256", digest.hexdigest())}"'
        response.headers["x-amz-version-id"] = str(doc.get("document_id", ""))
        return response
    finally:
        spool.close()


@bp.route("/admin/s3-overlay", methods=["GET", "POST"])
@login_required
def manage():
    if not is_admin(g.user):
        abort(403)
    if not current_app.config.get("S3_OVERLAY_ENABLED", False):
        flash("S3-Overlay ist deaktiviert. SIMPLEOFFICE_S3_OVERLAY_ENABLED=true setzt es frei.")
    generated = None
    if request.method == "POST":
        action = request.form.get("action", "create")
        try:
            if action == "revoke":
                if not credentials.revoke(str(g.user["username"]), request.form.get("access_key", "")):
                    raise ValueError("S3-Zugang nicht gefunden.")
                audit("s3_credential_revoked", "s3-credential", request.form.get("access_key", ""))
                flash("S3-Zugang widerrufen.")
            else:
                scopes = request.form.getlist("scopes")
                generated = credentials.create(str(g.user["username"]), request.form.get("label", ""), scopes,
                                               request.form.get("prefix", ""), int(request.form.get("expires_days", "90")))
                audit("s3_credential_created", "s3-credential", generated["access_key"], detail={"label": generated["label"], "scopes": generated["scopes"], "prefix": generated["prefix"], "expires_at": generated["expires_at"]})
                flash("S3-Zugang erstellt. Secret jetzt sicher kopieren; es wird nicht erneut angezeigt.")
        except (OSError, ValueError, TypeError) as exc:
            flash(str(exc))
    return render_template("admin/s3_overlay.html", credentials=credentials.list_for(str(g.user["username"])), generated=generated,
                           enabled=bool(current_app.config.get("S3_OVERLAY_ENABLED", False)), endpoint=request.url_root.rstrip("/") + S3_PREFIX,
                           region=current_app.config.get("S3_OVERLAY_REGION", "us-east-1"), bucket=BUCKET)
