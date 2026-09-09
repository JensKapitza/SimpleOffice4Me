"""Central password hashing policy.

New passwords and app credentials use Argon2id. Legacy Werkzeug and raw-scrypt
records remain verifiable only to support a safe migration after successful
authentication; no legacy algorithm is used for newly stored credentials.
"""
from __future__ import annotations

import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from werkzeug.security import check_password_hash

# Argon2id parameters are encoded into every hash, allowing future policy upgrades.
_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password must not be empty")
    return _PASSWORD_HASHER.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify Argon2id and supported legacy Werkzeug password hashes."""
    value = str(stored_hash or "")
    if value.startswith("$argon2id$"):
        try:
            return bool(_PASSWORD_HASHER.verify(value, password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    if value.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(value, password)
    return False


def verify_legacy_raw_scrypt(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Verify the historical file-store credential format for migration only.

    This legacy scrypt call must never be used to create new credentials. It is
    intentionally retained so existing CalDAV/CardDAV/WebDAV/share passwords
    can be upgraded to Argon2id after successful authentication.
    """
    try:
        salt = bytes.fromhex(str(salt_hex))
        expected = bytes.fromhex(str(hash_hex))
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def verify_password_record(record: dict, password: str, *, hash_key: str = "password_hash", salt_key: str = "password_salt") -> bool:
    """Verify an Argon2id record or the historical raw-scrypt record."""
    stored = str(record.get(hash_key, ""))
    if stored.startswith("$argon2id$"):
        return verify_password(stored, password)
    salt = str(record.get(salt_key, ""))
    return bool(salt and stored and verify_legacy_raw_scrypt(password, salt, stored))


def password_needs_rehash(stored_hash: str) -> bool:
    """Return true for legacy hashes or Argon2id hashes below current policy."""
    value = str(stored_hash or "")
    if not value.startswith("$argon2id$"):
        return True
    try:
        return _PASSWORD_HASHER.check_needs_rehash(value)
    except InvalidHashError:
        return True
