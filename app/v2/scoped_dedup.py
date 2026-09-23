"""Session-scoped equality tokens for V2 federation deduplication.

The protocol deliberately avoids exposing stable block hashes across peers. A
shared federation secret authenticates a short-lived session and derives
per-session HMAC equality tokens. The underlying SHA-512 block digest remains a
local integrity identifier only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
from typing import Any, Mapping


SCHEMA = "simpleoffice-v2-scoped-dedup/v1"
DEFAULT_SESSION_TTL = 300
_MAX_SESSION_TTL = 900
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX128 = re.compile(r"^[0-9a-f]{128}$")
_SESSION = re.compile(r"^v1\.([0-9]{1,12})\.([A-Za-z0-9_-]{22})\.([0-9a-f]{64})$")
_SESSION_DOMAIN = b"simpleoffice-v2-dedup-session\x00"
_KEY_DOMAIN = b"simpleoffice-v2-dedup-key\x00"
_BLOCK_DOMAIN = b"block\x00"


def _secret_bytes(shared_secret: str | bytes) -> bytes:
    value = shared_secret if isinstance(shared_secret, bytes) else str(shared_secret or "").encode("utf-8")
    if not value:
        raise ValueError("federation dedup secret is required")
    return value


def _normalize_blob_hash(value: str) -> str:
    digest = str(value or "").removeprefix("sha256:").strip().casefold()
    if not _HEX64.fullmatch(digest):
        raise ValueError("invalid blob sha256")
    return digest


def _normalize_block_hash(value: str) -> str:
    digest = str(value or "").removeprefix("sha512:").strip().casefold()
    if not _HEX128.fullmatch(digest):
        raise ValueError("invalid block sha512")
    return digest


def _nonce() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("=")


def create_dedup_session(
    shared_secret: str | bytes,
    blob_hash: str,
    *,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL,
) -> str:
    secret = _secret_bytes(shared_secret)
    digest = _normalize_blob_hash(blob_hash)
    issued_at = int(time.time()) if now is None else int(now)
    ttl = max(30, min(int(ttl_seconds), _MAX_SESSION_TTL))
    expires_at = issued_at + ttl
    unsigned = f"v1.{expires_at}.{_nonce()}"
    signature = hmac.new(
        secret,
        _SESSION_DOMAIN + digest.encode("ascii") + b"\x00" + unsigned.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{unsigned}.{signature}"


def validate_dedup_session(
    shared_secret: str | bytes,
    blob_hash: str,
    session: str,
    *,
    now: int | None = None,
) -> int:
    secret = _secret_bytes(shared_secret)
    digest = _normalize_blob_hash(blob_hash)
    match = _SESSION.fullmatch(str(session or ""))
    if match is None:
        raise ValueError("invalid federation dedup session")
    expires_at = int(match.group(1))
    unsigned = ".".join(str(session).split(".")[:3])
    expected = hmac.new(
        secret,
        _SESSION_DOMAIN + digest.encode("ascii") + b"\x00" + unsigned.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, match.group(3)):
        raise ValueError("invalid federation dedup session")
    checked_at = int(time.time()) if now is None else int(now)
    if expires_at <= checked_at:
        raise ValueError("expired federation dedup session")
    if expires_at - checked_at > _MAX_SESSION_TTL:
        raise ValueError("invalid federation dedup session lifetime")
    return expires_at


def scoped_block_token(shared_secret: str | bytes, session: str, block_sha512: str) -> str:
    secret = _secret_bytes(shared_secret)
    if _SESSION.fullmatch(str(session or "")) is None:
        raise ValueError("invalid federation dedup session")
    digest = _normalize_block_hash(block_sha512)
    session_key = hmac.new(secret, _KEY_DOMAIN + str(session).encode("ascii"), hashlib.sha256).digest()
    return hmac.new(session_key, _BLOCK_DOMAIN + bytes.fromhex(digest), hashlib.sha256).hexdigest()


def scoped_manifest_valid(
    manifest: Mapping[str, Any],
    *,
    shared_secret: str | bytes,
    blob_hash: str,
    now: int | None = None,
) -> bool:
    try:
        if manifest.get("schema") != SCHEMA:
            return False
        session = str(manifest["session"])
        validate_dedup_session(shared_secret, blob_hash, session, now=now)
        size = int(manifest["size"])
        blocks = manifest["blocks"]
        if size < 0 or not isinstance(blocks, list) or int(manifest["block_count"]) != len(blocks):
            return False
        if any(key in manifest for key in ("sha512", "file_sha512", "hash_algorithm")):
            return False
        offset = 0
        for index, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                return False
            length = int(block["length"])
            token = str(block["token"])
            if (
                int(block["index"]) != index
                or int(block["offset"]) != offset
                or length <= 0
                or not _HEX64.fullmatch(token)
                or any(key in block for key in ("sha512", "hash"))
            ):
                return False
            offset += length
        return offset == size
    except (KeyError, TypeError, ValueError):
        return False
