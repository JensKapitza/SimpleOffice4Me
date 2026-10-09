"""S3 path-style protocol endpoints and credential administration."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
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
from app.v3_inbox import record_completed_best_effort

from . import auth, credentials
from app.settings_store import SettingsStore
from .objects import DocumentObjects
from .multipart import MultipartError, MultipartStore
from .xml import document as xml_document, element, error as xml_error


bp = Blueprint("s3_overlay", __name__)
S3_PREFIX = "/s3"
BUCKET = "simpleoffice"


def _s3_settings_store():
    return SettingsStore(current_app.config["DOCUMENT_ROOT"])


def _s3_is_enabled():
    store = _s3_settings_store()
    if not store.path.exists():
        return current_app.config.get("S3_OVERLAY_ENABLED", False) is True
    return store.settings().get("s3", {}).get("enabled", False) is True


class S3Error(Exception):
    def __init__(self, code: str, message: str, status: int, headers: dict[str, str] | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.headers = headers or {}


def _enabled() -> bool:
    if not _s3_is_enabled():
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
    documents_enabled = has_feature(row, "documents")
    contacts_enabled = has_feature(row, "contacts")
    calendar_enabled = has_feature(row, "calendar")
    projects_enabled = has_feature(row, "projects")
    mail_enabled = has_feature(row, "mail")
    if not (documents_enabled or contacts_enabled or calendar_enabled or projects_enabled or mail_enabled):
        raise S3Error("AccessDenied", "S3 access is not available", 403)
    if required_scope == "inbox:put" and not documents_enabled:
        raise S3Error("AccessDenied", "Inbox uploads require document access", 403)
    if required_scope and required_scope not in identity["scopes"]:
        raise S3Error("AccessDenied", "The S3 credential lacks the required scope", 403)
    key_prefix = identity.get("prefix", "")
    if key_prefix and request.view_args and request.view_args.get("key"):
        if not request.view_args["key"].startswith(key_prefix):
            raise S3Error("NoSuchKey", "The specified key does not exist", 404)
    return identity, DocumentObjects(identity["username"], documents_enabled=documents_enabled,
                                     contacts_enabled=contacts_enabled, calendar_enabled=calendar_enabled,
                                     projects_enabled=projects_enabled, mail_enabled=mail_enabled)


def _multipart_store() -> MultipartStore:
    return MultipartStore(current_app.config["DOCUMENT_ROOT"])


def _multipart_request() -> bool:
    return any(name in request.args for name in ("uploads", "uploadId", "partNumber"))


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


@bp.route("/s3/<bucket>", defaults={"key": ""}, methods=["GET", "HEAD", "PUT", "POST", "DELETE"], strict_slashes=False)
@bp.route("/s3/<bucket>/<path:key>", methods=["GET", "HEAD", "PUT", "POST", "DELETE"])
def bucket_object(bucket: str, key: str):
    method = request.method
    multipart = _multipart_request()
    bucket_metadata = not key and (
        method == "HEAD"
        or (method == "GET" and bool({"location", "versioning"} & set(request.args)))
    )
    required_scope = None if bucket_metadata else ("inbox:put" if multipart or method == "PUT" else "read")
    identity, provider = _authenticate(required_scope)
    if bucket != BUCKET:
        raise S3Error("NoSuchBucket", "The specified bucket does not exist", 404)
    if not key:
        if bucket_metadata and not ({"read", "inbox:put"} & set(identity["scopes"])):
            raise S3Error("AccessDenied", "The S3 credential lacks bucket access", 403)
        if method == "HEAD":
            return _response(b"", 200)
        if method == "GET" and "location" in request.args:
            region = current_app.config.get("S3_OVERLAY_REGION", "us-east-1")
            return _response(xml_document("LocationConstraint", escape(region)))
        if method == "GET" and "versioning" in request.args:
            return _response(xml_document("VersioningConfiguration", ""))
        if method == "GET" and "uploads" in request.args:
            return _list_multipart_uploads(identity)
        if method == "GET":
            return _list_objects(provider, identity)
        raise S3Error("MethodNotAllowed", "The requested bucket operation is not supported", 405)
    if method == "PUT":
        if "uploadId" in request.args or "partNumber" in request.args:
            return _put_multipart_part(identity, key)
        return _put_inbox(provider, identity, key)
    if method == "POST" and "uploads" in request.args:
        return _initiate_multipart(identity, key)
    if method == "POST" and "uploadId" in request.args:
        return _complete_multipart(identity, provider, key)
    if method == "DELETE" and "uploadId" in request.args:
        return _abort_multipart(identity, key)
    if method == "GET" and "uploadId" in request.args:
        return _list_multipart_parts(identity, key)
    if method in {"POST", "DELETE"}:
        raise S3Error("AccessDenied", "This S3 mutation is not supported", 403)
    return _get_object(provider, identity, key, head=method == "HEAD")


def _cursor_decode(token: str, *, prefix: str, delimiter: str, access_key: str) -> str:
    try:
        payload_text, mac_text = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4))
        expected = hmac.new(bytes(current_app.config["SECRET_KEY"], "utf-8") if isinstance(current_app.config["SECRET_KEY"], str) else current_app.config["SECRET_KEY"], payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, mac_text):
            raise ValueError("bad token")
        value = json.loads(payload.decode("utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("v") != 2
            or not isinstance(value.get("key"), str)
            or value.get("prefix") != prefix
            or value.get("delimiter") != delimiter
            or value.get("access") != access_key
        ):
            raise ValueError("bad token")
        return value["key"]
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise S3Error("InvalidArgument", "The continuation token is invalid", 400) from exc


def _cursor_encode(key: str, *, prefix: str, delimiter: str, access_key: str) -> str:
    payload = json.dumps(
        {"v": 2, "key": key, "prefix": prefix, "delimiter": delimiter, "access": access_key},
        separators=(",", ":"),
    ).encode()
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
    scan_prefix = prefix
    prefix_disjoint = False
    if access_prefix:
        if prefix.startswith(access_prefix):
            scan_prefix = prefix
        elif access_prefix.startswith(prefix):
            scan_prefix = access_prefix
        else:
            prefix_disjoint = True
    delimiter = request.args.get("delimiter", "")
    if delimiter and len(delimiter) > 1:
        raise S3Error("InvalidArgument", "delimiter must be one character", 400)
    if legacy:
        after = request.args.get("marker", "")
    else:
        after = (
            _cursor_decode(
                request.args["continuation-token"],
                prefix=prefix,
                delimiter=delimiter,
                access_key=identity["access_key"],
            )
            if request.args.get("continuation-token")
            else request.args.get("start-after", "")
        )
    encoding_type = request.args.get("encoding-type")
    if encoding_type not in {None, "url"}:
        raise S3Error("InvalidArgument", "encoding-type must be url", 400)
    entries = []
    seen_common = set()
    seen_entries = set()
    last_key = after
    scan_after = after
    truncated = False
    batch_size = max(100, min(1000, count + 1))
    if count and not prefix_disjoint:
        while True:
            rows = provider.keys(prefix=scan_prefix, after=scan_after, limit=batch_size)
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
    encoding = encoding_type == "url"
    def key_text(value: str) -> str:
        return quote(value, safe="/-_.~") if encoding else value
    parts = [element("Name", BUCKET), element("Prefix", key_text(prefix)), element("KeyCount", len(entries)), element("MaxKeys", count), element("IsTruncated", str(truncated).lower())]
    if delimiter:
        parts.append(element("Delimiter", key_text(delimiter)))
    if legacy:
        parts.append(element("Marker", key_text(request.args.get("marker", ""))))
    else:
        if request.args.get("continuation-token"):
            parts.append(element("ContinuationToken", request.args["continuation-token"]))
        if request.args.get("start-after"):
            parts.append(element("StartAfter", key_text(request.args["start-after"])))
    if truncated and last_key:
        if legacy:
            if delimiter:
                parts.append(element("NextMarker", key_text(last_key)))
        else:
            token = _cursor_encode(
                last_key,
                prefix=prefix,
                delimiter=delimiter,
                access_key=identity["access_key"],
            )
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
    if (key.startswith("_meta/") or key.startswith("documents/") or key.startswith("contacts/")
            or key.startswith("invoices/")
            or key.startswith("calendar/") or key.startswith("tasks/") or key.startswith("projects/")
            or key.startswith("objects/") or key.startswith("email/") or key.startswith("exports/")):
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
    unsupported_args = {"acl", "tagging", "uploads", "uploadId", "partNumber", "delete"}
    if unsupported_args.intersection(request.args):
        raise S3Error("AccessDenied", "This S3 object mutation is not supported", 403)
    _reject_copy_acl_headers()
    expected_hash = request.headers.get("X-Amz-Content-Sha256", "")
    if not expected_hash or not all(ch in "0123456789abcdefABCDEF" for ch in expected_hash) or len(expected_hash) != 64:
        raise S3Error("InvalidRequest", "A SHA-256 signed payload is required", 400)
    checksum_header = request.headers.get("X-Amz-Checksum-Sha256", "")
    expected_checksum = b""
    if checksum_header:
        try:
            expected_checksum = base64.b64decode(checksum_header, validate=True)
        except (ValueError, TypeError) as exc:
            raise S3Error("InvalidRequest", "x-amz-checksum-sha256 must be valid base64", 400) from exc
        if len(expected_checksum) != hashlib.sha256().digest_size:
            raise S3Error("InvalidRequest", "x-amz-checksum-sha256 has an invalid length", 400)
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
        if expected_checksum and not hmac.compare_digest(digest.digest(), expected_checksum):
            raise S3Error("BadDigest", "The request body does not match x-amz-checksum-sha256", 400)
        spool.seek(0)
        try:
            _scan_inbox_upload(spool, identity, key)
            spool.seek(0)
            doc = provider.put_inbox(key[len("inbox/"):], spool, limit, digest.hexdigest())
        except FileExistsError as exc:
            raise S3Error("PreconditionFailed", "The inbox key already exists", 412) from exc
        except ValueError as exc:
            raise S3Error("InvalidArgument", "The inbox key or file could not be imported", 400) from exc
        actor = get_db().execute("SELECT id, username FROM user WHERE username=?", (identity["username"],)).fetchone()
        audit("s3_inbox_uploaded", "s3-inbox-upload", str(doc.get("document_id", "")),
              detail={"key": key, "source": "s3-inbox", "size": total, "sha256": digest.hexdigest(),
                      "access_key_id": identity["access_key"]}, actor=actor)
        record_completed_best_effort(
            current_app.config["DOCUMENT_ROOT"],
            source="s3",
            source_key=key,
            original_name=key[len("inbox/"):],
            actor=identity["username"],
            document_id=str(doc.get("document_id", "")),
            sha256=str(doc.get("sha256") or digest.hexdigest()),
            size=int(doc.get("size") or total),
            malware_status="clean" if current_app.config.get("WEBDAV_UPLOAD_SCAN", False) else "not_configured",
        )
        response = _response(b"", 200)
        response.headers["ETag"] = f'"{doc.get("sha256", digest.hexdigest())}"'
        response.headers["x-amz-version-id"] = str(doc.get("document_id", ""))
        if checksum_header:
            response.headers["x-amz-checksum-sha256"] = base64.b64encode(digest.digest()).decode("ascii")
        return response
    finally:
        spool.close()


def _scan_inbox_upload(spool, identity: dict, key: str) -> None:
    if not current_app.config.get("WEBDAV_UPLOAD_SCAN", False):
        return
    from app.attachment_security import AttachmentSecurity, QuarantineCapacityError

    spool.seek(0)
    try:
        result = AttachmentSecurity(current_app.config["DOCUMENT_ROOT"]).scan_webdav_upload(
            spool, f"s3:{identity['username']}", key,
            max(1, int(current_app.config.get("WEBDAV_QUARANTINE_BYTES", 200 * 1024 * 1024))),
            source_type="s3-inbox",
        )
    except QuarantineCapacityError as exc:
        raise S3Error("ServiceUnavailable", "The upload scan quarantine is full", 507) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise S3Error("ServiceUnavailable", "Malware scanner unavailable; upload was not published", 503,
                      {"Retry-After": "60"}) from exc
    if result.get("verdict") != "clean":
        raise S3Error("AccessDenied", "Malware detected; the upload was quarantined", 422)
    spool.seek(0)


def _reject_copy_acl_headers() -> None:
    lowered = {name.casefold() for name in request.headers.keys()}
    if ("x-amz-copy-source" in lowered or "x-amz-tagging" in lowered or "x-amz-acl" in lowered
            or any(name.startswith("x-amz-grant-") for name in lowered)):
        raise S3Error("AccessDenied", "Copy, tagging and ACL mutations are not supported", 403)


def _reject_unsupported_multipart_args(allowed: set[str]) -> None:
    unsupported = {name.casefold() for name in request.args} - allowed
    if unsupported.intersection({"acl", "tagging", "delete", "uploads", "uploadid", "partnumber"}):
        raise S3Error("AccessDenied", "This multipart operation is not supported", 403)


def _initiate_multipart(identity: dict, key: str):
    _reject_copy_acl_headers()
    _reject_unsupported_multipart_args({"uploads"})
    store = _multipart_store()
    try:
        store.validate_key(key)
        upload_id = store.initiate(key, identity["username"], identity["access_key"])
    except MultipartError as exc:
        raise S3Error("InvalidArgument", str(exc), 400) from exc
    body = element("Bucket", BUCKET) + element("Key", key) + element("UploadId", upload_id)
    return _response(xml_document("InitiateMultipartUploadResult", body))


def _put_multipart_part(identity: dict, key: str):
    _reject_copy_acl_headers()
    _reject_unsupported_multipart_args({"uploadid", "partnumber"})
    upload_id = request.args.get("uploadId", "")
    raw_number = request.args.get("partNumber", "")
    try:
        part_number = int(raw_number)
    except ValueError as exc:
        raise S3Error("InvalidArgument", "partNumber must be an integer", 400) from exc
    limit = int(current_app.config.get("S3_OVERLAY_MAX_UPLOAD_BYTES", current_app.config.get("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)))
    expected_hash = request.headers.get("X-Amz-Content-Sha256", "")
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", expected_hash):
        raise S3Error("InvalidRequest", "A SHA-256 signed payload is required", 400)
    if request.content_length is not None and request.content_length > limit:
        raise S3Error("EntityTooLarge", "The part exceeds the configured upload limit", 413)
    try:
        etag, _size = _multipart_store().put_part(
            key, upload_id, part_number, identity["username"], identity["access_key"],
            request.stream, limit, expected_hash,
        )
    except MultipartError as exc:
        code, status = ("NoSuchUpload", 404) if "does not exist" in str(exc) else ("InvalidPart", 400)
        raise S3Error(code, str(exc), status) from exc
    response = _response(b"")
    response.headers["ETag"] = f'"{etag}"'
    return response


def _complete_multipart(identity: dict, provider: DocumentObjects, key: str):
    _reject_copy_acl_headers()
    _reject_unsupported_multipart_args({"uploadid"})
    upload_id = request.args.get("uploadId", "")
    limit = int(current_app.config.get("S3_OVERLAY_MAX_UPLOAD_BYTES", current_app.config.get("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)))
    if request.content_length is not None and request.content_length > 1024 * 1024:
        raise S3Error("EntityTooLarge", "The multipart manifest is too large", 413)
    body = request.get_data(cache=True)
    expected_hash = request.headers.get("X-Amz-Content-Sha256", "")
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", expected_hash) or hashlib.sha256(body).hexdigest() != expected_hash.casefold():
        raise S3Error("BadDigest", "The multipart manifest does not match its signed SHA-256", 400)
    spool = None
    store = _multipart_store()
    completion_claimed = False
    completed = False
    try:
        spool, digest, total, etag = store.complete(
            key, upload_id, identity["username"], identity["access_key"], body, limit,
        )
        completion_claimed = True
        spool.seek(0)
        _scan_inbox_upload(spool, identity, key)
        spool.seek(0)
        doc = provider.put_inbox(key[len("inbox/"):], spool, limit, digest)
        actor = get_db().execute("SELECT id, username FROM user WHERE username=?", (identity["username"],)).fetchone()
        audit("s3_inbox_uploaded", "s3-inbox-upload", str(doc.get("document_id", "")),
              detail={"key": key, "source": "s3-inbox-multipart", "size": total, "sha256": digest,
                      "access_key_id": identity["access_key"]}, actor=actor)
        store.finish(upload_id, identity["username"], identity["access_key"], key)
        completed = True
    except FileExistsError as exc:
        raise S3Error("PreconditionFailed", "The inbox key already exists", 412) from exc
    except MultipartError as exc:
        message = str(exc)
        if "does not exist" in message:
            code, status = "NoSuchUpload", 404
        elif "already completing" in message:
            code, status = "OperationAborted", 409
        else:
            code, status = "InvalidPart", 400
        raise S3Error(code, str(exc), status) from exc
    except ValueError as exc:
        raise S3Error("InvalidArgument", "The completed inbox upload could not be imported", 400) from exc
    finally:
        if spool is not None:
            spool.close()
        if completion_claimed and not completed:
            try:
                store.release_completion(upload_id, identity["username"], identity["access_key"], key)
            except (MultipartError, OSError):
                current_app.logger.warning("Could not release failed S3 multipart completion state", exc_info=True)
    location = request.url_root.rstrip("/") + S3_PREFIX + "/" + BUCKET + "/" + quote(key, safe="/")
    response = _response(xml_document("CompleteMultipartUploadResult",
        element("Location", location) + element("Bucket", BUCKET) + element("Key", key) + element("ETag", f'"{etag}"')))
    response.headers["ETag"] = f'"{etag}"'
    return response


def _abort_multipart(identity: dict, key: str):
    try:
        _multipart_store().abort(request.args.get("uploadId", ""), identity["username"], identity["access_key"], key)
    except MultipartError as exc:
        raise S3Error("NoSuchUpload", str(exc), 404) from exc
    return _response(b"", 204)


def _multipart_page_size(name: str, default: int) -> int:
    try:
        value = int(request.args.get(name, str(default)))
    except ValueError as exc:
        raise S3Error("InvalidArgument", f"{name} must be an integer", 400) from exc
    if not 0 <= value <= 1000:
        raise S3Error("InvalidArgument", f"{name} must be between 0 and 1000", 400)
    return value


def _list_multipart_parts(identity: dict, key: str):
    marker = _multipart_page_size("part-number-marker", 0)
    max_parts = _multipart_page_size("max-parts", 1000)
    if max_parts == 0:
        raise S3Error("InvalidArgument", "max-parts must be at least 1", 400)
    try:
        all_rows = _multipart_store().list_parts(request.args.get("uploadId", ""), identity["username"], identity["access_key"], key)
    except MultipartError as exc:
        raise S3Error("NoSuchUpload", str(exc), 404) from exc
    candidates = [row for row in all_rows if row["part_number"] > marker]
    rows = candidates[:max_parts]
    truncated = len(candidates) > len(rows)
    next_marker = rows[-1]["part_number"] if truncated and rows else marker
    pieces = [element("Bucket", BUCKET), element("Key", key), element("UploadId", request.args.get("uploadId", "")),
              element("PartNumberMarker", marker), element("NextPartNumberMarker", next_marker),
              element("MaxParts", max_parts), element("IsTruncated", str(truncated).lower())]
    for row in rows:
        updated = datetime.fromtimestamp(row.get("updated_at", 0), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        pieces.append("<Part>" + element("PartNumber", row["part_number"]) + element("LastModified", updated)
                      + element("ETag", f'"{row["etag"]}"') + element("Size", row["size"]) + "</Part>")
    return _response(xml_document("ListPartsResult", "".join(pieces)))


def _list_multipart_uploads(identity: dict):
    prefix = request.args.get("prefix", "")
    max_uploads = _multipart_page_size("max-uploads", 1000)
    if max_uploads == 0:
        raise S3Error("InvalidArgument", "max-uploads must be at least 1", 400)
    key_marker = request.args.get("key-marker", "")
    upload_marker = request.args.get("upload-id-marker", "")
    all_rows = _multipart_store().list_uploads(identity["username"], identity["access_key"], prefix)
    candidates = [row for row in all_rows if (row["key"], row["upload_id"]) > (key_marker, upload_marker)]
    rows = candidates[:max_uploads]
    truncated = len(candidates) > len(rows)
    next_key = rows[-1]["key"] if truncated and rows else ""
    next_upload = rows[-1]["upload_id"] if truncated and rows else ""
    pieces = [element("Bucket", BUCKET), element("KeyMarker", key_marker), element("UploadIdMarker", upload_marker),
              element("NextKeyMarker", next_key), element("NextUploadIdMarker", next_upload),
              element("Prefix", prefix), element("MaxUploads", max_uploads), element("IsTruncated", str(truncated).lower())]
    for row in rows:
        pieces.append("<Upload>" + element("Key", row["key"]) + element("UploadId", row["upload_id"])
                      + "<Initiator>" + element("ID", identity["username"]) + "</Initiator>"
                      + "<Owner>" + element("ID", identity["username"]) + "</Owner>"
                      + element("StorageClass", "STANDARD") + "</Upload>")
    return _response(xml_document("ListMultipartUploadsResult", "".join(pieces)))


@bp.route("/admin/s3-overlay", methods=["GET", "POST"])
@login_required
def manage():
    if not is_admin(g.user):
        abort(403)

    generated = None
    if request.method == "POST":
        action = request.form.get("action", "create")
        try:
            if action == "toggle":
                value = request.form.get("enabled")
                if value not in {"0", "1"}:
                    raise ValueError("Ungültiger S3-Status.")
                enabled = value == "1"
                store = _s3_settings_store()
                settings = store.settings()
                settings["s3"] = {"enabled": enabled}
                store.save(settings, str(g.user["username"]))
                audit("s3_overlay_toggled", "s3-overlay", "global", detail={"enabled": enabled})
                flash("S3-Overlay aktiviert." if enabled else "S3-Overlay deaktiviert.")
            elif action == "revoke":
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
                           enabled=_s3_is_enabled(), endpoint=request.url_root.rstrip("/") + S3_PREFIX,
                           region=current_app.config.get("S3_OVERLAY_REGION", "us-east-1"), bucket=BUCKET)
