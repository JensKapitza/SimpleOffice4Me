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
from collections import OrderedDict, deque

from flask import Blueprint, abort, jsonify, request

from .github_error_reporter import REPORT_SCHEMA, load_config, report_payload_to_github, sanitize_text


bp = Blueprint("error_relay", __name__, url_prefix="/api/error-reports/v1")
MAX_REPORT_BYTES = 16 * 1024
RATE_WINDOW_SECONDS = 60
RATE_LIMIT_PER_SOURCE = 60
RATE_LIMIT_GLOBAL = 300
RATE_SOURCE_BUCKET_LIMIT = 4096
MAX_UPSTREAM_INFLIGHT = 4
UPSTREAM_WINDOW_SECONDS = 60 * 60
UPSTREAM_LIMIT_GLOBAL = 120
CACHE_LIMIT = 5000
_ALLOWED_KEYS = {
    "schema", "request_id", "fingerprint", "exception_type", "endpoint",
    "method", "app_version", "frames",
}
_TOKEN = re.compile(r"[A-Za-z0-9_.:+-]{1,160}\Z")
_FINGERPRINT = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z")
_FRAME_NAME = re.compile(r"[A-Za-z0-9_.:+<> -]{1,160}\Z")
_TEMPLATE_NAME = re.compile(r"[A-Za-z0-9_./:+-]{1,240}\Z")
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_rate_lock = threading.Lock()
_rate_salt = secrets.token_bytes(32)
_rate_by_source: dict[str, deque[float]] = {}
_rate_global: deque[float] = deque()
_cache_lock = threading.Lock()
_issue_cache: OrderedDict[str, int] = OrderedDict()
_upstream_lock = threading.Lock()
_upstream_fingerprints: set[str] = set()
_upstream_attempts: deque[float] = deque()


def relay_enabled() -> bool:
    return os.environ.get("SIMPLEOFFICE_ERROR_RELAY_ENABLED", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _restrict_public_relay_surface() -> None:
    """Fail closed if the dedicated relay image later gains extra routes."""
    if os.environ.get("SIMPLEOFFICE_CONTAINER_ROLE", "web").strip() != "error-relay":
        return
    allowed = (
        request.path == "/api/error-reports/v1/health"
        and request.method in {"GET", "HEAD"}
    ) or (
        request.path == "/api/error-reports/v1/reports"
        and request.method == "POST"
    )
    if not allowed:
        abort(404)


@bp.before_request
def restrict_public_relay_surface():
    _restrict_public_relay_surface()


def _source_key() -> str:
    # Keep no raw client address in relay state or logs.
    address = str(request.remote_addr or "unknown").encode("utf-8", "replace")
    return hashlib.blake2s(address, key=_rate_salt, digest_size=16).hexdigest()


def _trim(bucket: deque[float], now: float) -> None:
    cutoff = now - RATE_WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()


def _cleanup_source_buckets(now: float) -> None:
    cutoff = now - RATE_WINDOW_SECONDS
    stale = [
        key for key, values in _rate_by_source.items()
        if not values or values[-1] <= cutoff
    ]
    for key in stale:
        _rate_by_source.pop(key, None)


def _rate_allowed() -> bool:
    now = time.monotonic()
    source = _source_key()
    with _rate_lock:
        _trim(_rate_global, now)
        # Reject at the global boundary before allocating attacker-controlled
        # source buckets. This keeps memory bounded even with many source keys.
        if len(_rate_global) >= RATE_LIMIT_GLOBAL:
            return False

        bucket = _rate_by_source.get(source)
        if bucket is not None:
            _trim(bucket, now)
            if len(bucket) >= RATE_LIMIT_PER_SOURCE:
                return False
        else:
            if len(_rate_by_source) >= RATE_SOURCE_BUCKET_LIMIT:
                _cleanup_source_buckets(now)
                if len(_rate_by_source) >= RATE_SOURCE_BUCKET_LIMIT:
                    return False
            bucket = deque()
            _rate_by_source[source] = bucket

        _rate_global.append(now)
        bucket.append(now)
        return True


def _reserve_upstream(fingerprint: str) -> str:
    """Reserve bounded GitHub capacity without blocking request threads."""
    now = time.monotonic()
    with _upstream_lock:
        if fingerprint in _upstream_fingerprints:
            return "duplicate"
        if len(_upstream_fingerprints) >= MAX_UPSTREAM_INFLIGHT:
            return "busy"
        cutoff = now - UPSTREAM_WINDOW_SECONDS
        while _upstream_attempts and _upstream_attempts[0] <= cutoff:
            _upstream_attempts.popleft()
        if len(_upstream_attempts) >= UPSTREAM_LIMIT_GLOBAL:
            return "limited"
        _upstream_fingerprints.add(fingerprint)
        _upstream_attempts.append(now)
        return "reserved"


def _release_upstream(fingerprint: str) -> None:
    with _upstream_lock:
        _upstream_fingerprints.discard(fingerprint)


def _bounded_token(value: object, limit: int) -> str:
    text = sanitize_text(value, limit)
    if not text or not _TOKEN.fullmatch(text):
        raise ValueError("invalid technical token")
    return text


def _frame_text(value: object, pattern: re.Pattern[str], limit: int) -> str:
    text = sanitize_text(value, limit)
    if not text or not pattern.fullmatch(text):
        raise ValueError("invalid stack coordinate")
    return text


def _validated_frames(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("invalid frames")
    result: list[dict[str, object]] = []
    for frame in value:
        if not isinstance(frame, dict):
            raise ValueError("invalid frame")
        line = frame.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 0 or line > 10_000_000:
            raise ValueError("invalid frame line")
        if "template" in frame:
            if set(frame) - {"template", "line", "variable"}:
                raise ValueError("frame contains unsupported fields")
            item: dict[str, object] = {
                "template": _frame_text(frame.get("template"), _TEMPLATE_NAME, 240),
                "line": line,
            }
            variable = sanitize_text(frame.get("variable"), 120)
            if variable:
                item["variable"] = _frame_text(variable, _FRAME_NAME, 120)
            else:
                item["variable"] = ""
            result.append(item)
        else:
            if set(frame) - {"file", "line", "function"}:
                raise ValueError("frame contains unsupported fields")
            result.append({
                "file": _frame_text(frame.get("file"), _FRAME_NAME, 160),
                "line": line,
                "function": _frame_text(frame.get("function"), _FRAME_NAME, 160),
            })
    return result


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

    return {
        "schema": REPORT_SCHEMA,
        "request_id": request_id,
        "fingerprint": fingerprint,
        "exception_type": exception_type,
        "endpoint": endpoint,
        "method": method,
        "app_version": app_version,
        "frames": _validated_frames(value.get("frames")),
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
    try:
        config = load_config()
        ready = bool(config.token and config.repository)
    except Exception:
        # Configuration errors are intentionally not reflected to anonymous callers.
        ready = False
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

    try:
        config = load_config()
    except Exception:
        return jsonify({"error": "relay not configured"}), 503
    if not config.token:
        return jsonify({"error": "relay not configured"}), 503

    reservation = _reserve_upstream(fingerprint)
    if reservation == "duplicate":
        return jsonify({"accepted": True, "pending": True, "deduplicated": True}), 202
    if reservation == "busy":
        response = jsonify({"error": "relay busy"})
        response.status_code = 503
        response.headers["Retry-After"] = "2"
        return response
    if reservation == "limited":
        response = jsonify({"error": "upstream rate limit exceeded"})
        response.status_code = 429
        response.headers["Retry-After"] = str(UPSTREAM_WINDOW_SECONDS)
        return response

    try:
        # Recheck after reservation in case a previous request populated the cache
        # between our first lookup and acquiring the upstream slot.
        cached = _cached_issue(fingerprint)
        if cached is not None:
            return jsonify({"accepted": True, "issue_number": cached, "deduplicated": True})
        try:
            issue_number = report_payload_to_github(config, payload)
        except Exception:
            # Do not reflect GitHub/token/network details to an anonymous caller.
            return jsonify({"error": "upstream unavailable"}), 502
        _remember_issue(fingerprint, issue_number)
        return jsonify({"accepted": True, "issue_number": issue_number, "deduplicated": False}), 202
    finally:
        _release_upstream(fingerprint)
