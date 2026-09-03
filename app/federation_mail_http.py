"""HTTP endpoints for privacy-first federated mail lookup and EML recovery."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from .federation_http import _authorized
from .federation_mail import MAX_LOCATE_QUERIES, eml_for_locator, locate_local
from .mail_client import MailStore, MAX_MESSAGE_BYTES

bp = Blueprint("federation_mail_http", __name__, url_prefix="/federation/v1/mails")
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


@bp.before_request
def authenticate():
    if not _authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _mail_store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], key)


def _range(value: str, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    if "," in value:
        raise ValueError("multiple ranges unsupported")
    match = _RANGE_RE.fullmatch(value.strip())
    if not match or size < 1:
        raise ValueError("invalid range")
    first, last = match.groups()
    if not first:
        length = int(last or "0")
        if length < 1:
            raise ValueError("invalid suffix")
        return max(0, size - length), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)


@bp.get("/capabilities")
def capabilities():
    return jsonify({
        "resource": "mails",
        "version": 1,
        "fingerprint_lookup": True,
        "raw_sha512": True,
        "normalized_content_sha512": True,
        "simhash64": True,
        "opaque_locator": True,
        "metadata_disclosure": False,
        "payload_requires_explicit_export": True,
        "range_download": True,
        "max_lookup_queries": MAX_LOCATE_QUERIES,
        "max_message_bytes": MAX_MESSAGE_BYTES,
    })


@bp.post("/locate")
def locate():
    payload = request.get_json(silent=True) or {}
    queries = payload.get("queries")
    if not isinstance(queries, list):
        return jsonify({"error": "invalid_queries"}), 400
    if len(queries) > MAX_LOCATE_QUERIES:
        return jsonify({"error": "too_many_queries", "maximum": MAX_LOCATE_QUERIES}), 413
    safe_queries: list[dict[str, Any]] = [row for row in queries if isinstance(row, dict)]
    matches = locate_local(current_app.config["DOCUMENT_ROOT"], safe_queries)
    return jsonify({"matches": matches, "metadata_disclosure": False})


@bp.route("/<locator>/eml", methods=["GET", "HEAD"])
def eml(locator: str):
    try:
        raw, _row = eml_for_locator(current_app.config["DOCUMENT_ROOT"], _mail_store(), locator)
    except PermissionError:
        return jsonify({"error": "not_exported"}), 403
    except (FileNotFoundError, KeyError, ValueError):
        return jsonify({"error": "not_found"}), 404
    if len(raw) > MAX_MESSAGE_BYTES:
        return jsonify({"error": "message_too_large"}), 413
    digest = hashlib.sha512(raw).hexdigest()
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store, no-transform",
        "Content-Type": "message/rfc822",
        "X-Content-SHA512": digest,
        "ETag": f'"sha512:{digest}"',
    }
    size = len(raw)
    try:
        selected = _range(request.headers.get("Range", ""), size)
    except ValueError:
        headers["Content-Range"] = f"bytes */{size}"
        return Response(status=416, headers=headers)
    if selected is None:
        start, end, status = 0, max(0, size - 1), 200
    else:
        start, end, status = selected[0], selected[1], 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    body = raw[start:end + 1] if size and request.method != "HEAD" else b""
    headers["Content-Length"] = str(len(raw[start:end + 1]) if size else 0)
    return Response(body, status=status, headers=headers)
