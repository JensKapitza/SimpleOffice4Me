"""Public, privacy-preserving error relay embedded in SimpleOffice4Me.

The relay is intentionally independent from federation. Remote SimpleOffice
installations do not receive GitHub credentials; they only POST a small,
strictly allow-listed technical report to this endpoint.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from collections import OrderedDict, defaultdict, deque
from typing import Mapping

from flask import Blueprint, abort, jsonify, request

from .github_error_reporter import REPORT_SCHEMA, load_config, report_payload_to_github, safe_frames, sanitize_text


bp = Blueprint("error_relay", __name__, url_prefix="/api/error-reports/v1")
MAX_REPORT_BYTES = 16 * 1024
RATE_WINDOW_SECONDS = 60
RATE_LIMIT_PER_SOURCE = 60
RATE_LIMIT_GLOBAL = 300
CACHE_LIMIT = 5000
_ALLOWED_KEYS = {
    "schema", "request_id", "fingerprint", "exception_type", "endpoint",
    "method", "app_version", "frames",
}
_TOKEN = re.compile(r"[A-Za-z0-9_.:+-]{1,160}\Z")
_FINGERPRINT = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_rate_lock = threading.Lock()
_rate_salt = secrets.token_bytes(32)
_rate_by_source: dict[str, deque[float]] = defaultdict(deque)
_rate_global: deque[float] = deque()
_cache_lock = threading.Lock()
_issue_cache: OrderedDict[str, int] = OrderedDict()


def relay_enabled() -> bool:
    return os.environ.get("SIMPLEOFFICE_ERROR_RELAY_ENABLED", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _source_key() -> str:
    # Keep no raw client address in relay state or logs.
    address = str(request.remote_addr or "unknown").encode("utf-8", "replace")
    return hashlib.blake2s(address, key=_rate_salt, digest_size=16).hexdigest()


def _trim(bucket: deque[float], now: float) -> None:
    cutoff = now - RATE_WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()


def _rate_allowed() -> bool:
    now = time.monotonic()
    source = _source_key()
    with _rate_lock:
        _trim(_rate_global, now)
        bucket = _rate_by_source[source]
        _trim(bucket, now)
        if len(_rate_global) >= RATE_LIMIT_GLOBAL or len(bucket) >= RATE_LIMIT_PER_SOURCE:
            return False
        _rate_global.append(now)
        bucket.append(now)
        # Avoid unbounded source-map growth behind scanners/botnets.
        if len(_rate_by_source) > 4096:
            stale = [key for key, values in _rate_by_source.items() if not values or values[-1] <= now - RATE_WINDOW_SECONDS]
            for key in stale[:2048]:
                _rate_by_source.pop(key, None)
        return True


def _bounded_token(value: object, limit: int) -> str:
    text = sanitize_text(value, limit)
    if not text or not _TOKEN.fullmatch(text):
        raise ValueError("invalid technical token")
    return text


def validate_report_payload(value: object) -> dict[str, object]:
    """Validate and rebuild the public report using a strict schema/allow-list."""
    if not isinstance(value, dict):
        raise ValueError("report must be a JSON object")
    if set(value) - _ALLOWED_KEYS:
        raise ValueError("report contains unsupported fields")
    if value.get("schema") != REPORT_SCHEMA:
        raise ValueError("unsupported report schema")

    request_id = _bounded_token(value.get("request_id"), 64)
    fingerprint = sanitize_text(value.get("fingerprint"), 128)
    if not _FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("invalid fingerprint")
    exception_type = _bounded_token(value.get("exception_type"), 120)
    endpoint = _bounded_token(value.get("endpoint"), 160)
    method = sanitize_text(value.get("method"), 12).upper()
    if method not in _METHODS:
        raise ValueError("invalid HTTP method")
    app_version = sanitize_text(value.get("app_version"), 120)
    if app_version and not _TOKEN.fullmatch(app_version):
        raise ValueError("invalid app version")

    frames_value = value.get("frames")
    if not isinstance(frames_value, list) or len(frames_value) > 12:
        raise ValueError("invalid frames")
    for frame in frames_value:
        if not isinstance(frame, dict):
            raise ValueError("invalid frame")
        allowed = {"template", "line", "variable"} if "template" in frame else {"file", "line", "function"}
        if set(frame) - allowed:
            raise ValueError("frame contains unsupported fields")
        line = frame.get("line")
        if not isinstance(line, int) or line < 0 or line > 10_000_000:
            raise ValueError("invalid frame line")

    return {
        "schema": REPORT_SCHEMA,
        "request_id": request_id,
        "fingerprint": fingerprint,
        "exception_type": exception_type,
        "endpoint": endpoint,
        "method": method,
        "app_version": app_version,
        "frames": safe_frames(frames_value),
    }


def _cached_issue(fingerprint: str) -> int | None:
    with _cache_lock:
        number = _issue_cache.get(fingerprint)
        if number is not None:
            _issue_cache.move_to_end(fingerprint)
        return number


def _remember_issue(fingerprint: str, issue_number: int) -> None:
    with _cache_lock:
        _issue_cache[fingerprint] = issue_number
        _issue_cache.move_to_end(fingerprint)
        while len(_issue_cache) > CACHE_LIMIT:
            _issue_cache.popitem(last=False)


@bp.get("/health")
def health():
    if not relay_enabled():
        abort(404)
    config = load_config()
    ready = bool(config.token and config.repository)
    response = jsonify({
        "service": "simpleoffice-error-relay",
        "schema": REPORT_SCHEMA,
        "ready": ready,
    })
    response.status_code = 200 if ready else 503
    return response


@bp.post("/reports")
def receive_report():
    if not relay_enabled():
        abort(404)
    if request.mimetype != "application/json":
        return jsonify({"error": "application/json required"}), 415
    if request.content_length is not None and request.content_length > MAX_REPORT_BYTES:
        return jsonify({"error": "report too large"}), 413
    if not _rate_allowed():
        response = jsonify({"error": "rate limit exceeded"})
        response.status_code = 429
        response.headers["Retry-After"] = str(RATE_WINDOW_SECONDS)
        return response

    raw = request.get_data(cache=True, as_text=False)
    if len(raw) > MAX_REPORT_BYTES:
        return jsonify({"error": "report too large"}), 413
    try:
        payload = validate_report_payload(request.get_json(silent=False))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid report"}), 400

    fingerprint = str(payload["fingerprint"])
    cached = _cached_issue(fingerprint)
    if cached is not None:
        return jsonify({"accepted": True, "issue_number": cached, "deduplicated": True})

    config = load_config()
    if not config.token:
        return jsonify({"error": "relay not configured"}), 503
    try:
        issue_number = report_payload_to_github(config, payload)
    except Exception:
        # Do not reflect GitHub/token/network details to an anonymous caller.
        return jsonify({"error": "upstream unavailable"}), 502
    _remember_issue(fingerprint, issue_number)
    return jsonify({"accepted": True, "issue_number": issue_number, "deduplicated": False}), 202
