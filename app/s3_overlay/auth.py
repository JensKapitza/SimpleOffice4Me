"""AWS Signature Version 4 verification for the S3 endpoint."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from flask import current_app, request

from . import credentials

_AUTH_PREFIX = "AWS4-HMAC-SHA256"
_MAX_AUTH_HEADER_LENGTH = 8192
_MAX_PRESIGN_SECONDS = 7 * 24 * 3600


class SignatureError(ValueError):
    """A request failed S3 credential or signature verification."""


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str) -> bytes:
    key = _hmac(("AWS4" + secret).encode("utf-8"), date)
    return _hmac(_hmac(_hmac(key, region), "s3"), "aws4_request")


def _canonical_uri() -> str:
    raw = request.environ.get("RAW_URI") or request.environ.get("REQUEST_URI") or request.full_path
    path = urlsplit(str(raw)).path or "/"
    return quote(unquote(path), safe="/-_.~")


def _canonical_query(*, omit_signature: bool = False) -> str:
    pairs = parse_qsl(request.query_string.decode("latin-1"), keep_blank_values=True)
    if omit_signature:
        pairs = [(key, value) for key, value in pairs if key.casefold() != "x-amz-signature"]
    encoded = [(quote(key, safe="-_.~"), quote(value, safe="-_.~")) for key, value in pairs]
    return "&".join(f"{key}={value}" for key, value in sorted(encoded))


def _canonical_headers(names: list[str]) -> str:
    rows = []
    for name in names:
        values = request.headers.getlist(name)
        if not values:
            raise SignatureError("Signed header is missing")
        value = ",".join(" ".join(item.strip().split()) for item in values)
        rows.append(f"{name}:{value}\n")
    return "".join(rows)


def _authorization_fields(value: str) -> dict[str, str]:
    value = value or ""
    if len(value) > _MAX_AUTH_HEADER_LENGTH:
        raise SignatureError("S3 authorization header is too large")
    if not value.startswith(_AUTH_PREFIX):
        raise SignatureError("S3 authentication required")
    remainder = value[len(_AUTH_PREFIX):]
    if not remainder or remainder[0] not in " \t":
        raise SignatureError("S3 authentication required")
    payload = remainder.lstrip(" \t")
    if not payload:
        raise SignatureError("Malformed S3 authorization header")
    fields = {}
    for part in payload.split(","):
        key, separator, item = part.strip().partition("=")
        if not separator or key in fields:
            raise SignatureError("Malformed S3 authorization header")
        fields[key] = item
    return fields


def _credential_scope(value: str) -> tuple[str, str, str, str]:
    parts = value.split("/")
    if len(parts) != 4 or parts[2:] != ["s3", "aws4_request"]:
        raise SignatureError("Invalid S3 credential scope")
    return parts[0], parts[1], parts[2], parts[3]


def _date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise SignatureError("Invalid S3 request date") from exc


def _verify_signature(access_key: str, secret: str, date_text: str, scope: str, names: list[str], provided: str, *, presigned: bool) -> None:
    day, region, service, terminator = _credential_scope(scope)
    if (service != "s3" or terminator != "aws4_request" or date_text[:8] != day
            or region != current_app.config.get("S3_OVERLAY_REGION", "us-east-1")):
        raise SignatureError("Invalid S3 credential scope")
    payload_hash = request.headers.get("X-Amz-Content-Sha256", "UNSIGNED-PAYLOAD")
    if payload_hash == "UNSIGNED-PAYLOAD" and request.method not in {"GET", "HEAD"}:
        raise SignatureError("Unsigned payload is not allowed for writes")
    if not re.fullmatch(r"[A-Fa-f0-9]{64}|UNSIGNED-PAYLOAD", payload_hash):
        raise SignatureError("Unsupported S3 payload signing mode")
    canonical = "\n".join((
        request.method,
        _canonical_uri(),
        _canonical_query(omit_signature=presigned),
        _canonical_headers(names),
        ";".join(names),
        payload_hash,
    ))
    string_to_sign = "\n".join(("AWS4-HMAC-SHA256", date_text, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    expected = hmac.new(_signing_key(secret, day, region), string_to_sign.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(provided or "").casefold()):
        raise SignatureError("S3 signature does not match")


def authenticate() -> dict:
    """Authenticate header or presigned GET/HEAD requests; return scoped key data."""
    query_auth = any(key.casefold().startswith("x-amz-") for key in request.args)
    if query_auth:
        if request.method not in {"GET", "HEAD"}:
            raise SignatureError("Presigned S3 requests are read-only")
        params = {key.casefold(): value for key, value in request.args.items()}
        if params.get("x-amz-algorithm") != "AWS4-HMAC-SHA256" or params.get("x-amz-security-token"):
            raise SignatureError("Invalid presigned S3 request")
        access, separator, scope = params.get("x-amz-credential", "").partition("/")
        if not separator:
            raise SignatureError("Invalid S3 credential scope")
        date_text = params.get("x-amz-date", "")
        expires = int(params.get("x-amz-expires", "0")) if params.get("x-amz-expires", "").isdigit() else 0
        signed_names = params.get("x-amz-signedheaders", "").split(";")
        signature = params.get("x-amz-signature", "")
        timestamp = _date(date_text).timestamp()
        now = time.time()
        allowed_skew = int(current_app.config.get("S3_OVERLAY_CLOCK_SKEW_SECONDS", 900))
        if expires < 1 or expires > _MAX_PRESIGN_SECONDS or timestamp - now > allowed_skew or now > timestamp + expires:
            raise SignatureError("Presigned S3 request has expired")
        names = [name.casefold() for name in signed_names]
        if names != sorted(set(names)) or "host" not in names:
            raise SignatureError("Invalid signed headers")
        full_scope = scope
    else:
        fields = _authorization_fields(request.headers.get("Authorization", ""))
        access, separator, scope = fields.get("Credential", "").partition("/")
        if not separator:
            raise SignatureError("Invalid S3 credential scope")
        date_text = request.headers.get("X-Amz-Date", "")
        if not date_text:
            raise SignatureError("S3 request date is required")
        timestamp = _date(date_text).timestamp()
        allowed_skew = int(current_app.config.get("S3_OVERLAY_CLOCK_SKEW_SECONDS", 900))
        if abs(time.time() - timestamp) > allowed_skew:
            raise SignatureError("S3 request date is outside the allowed clock skew")
        names = fields.get("SignedHeaders", "").split(";")
        if names != sorted(set(names)) or "host" not in names:
            raise SignatureError("Invalid signed headers")
        full_scope = scope
        signature = fields.get("Signature", "")
    if not access or not re.fullmatch(r"[A-Za-z0-9]{12,64}", access):
        raise SignatureError("Unknown S3 access key")
    row = credentials.get(access, include_secret=True)
    if row is None:
        raise SignatureError("Unknown or expired S3 access key")
    _verify_signature(access, row["secret_key"], date_text, full_scope, names, signature, presigned=query_auth)
    credentials.mark_used(access)
    return row
