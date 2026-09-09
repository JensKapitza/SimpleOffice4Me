"""Password hashing policy for interactive user credentials.

New passwords use Argon2id. Legacy Werkzeug scrypt/PBKDF2 hashes remain readable
so existing installations can migrate transparently after a successful login.
"""
from __future__ import annotations

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


def password_needs_rehash(stored_hash: str) -> bool:
    """Return true for legacy hashes or Argon2id hashes below current policy."""
    value = str(stored_hash or "")
    if not value.startswith("$argon2id$"):
        return True
    try:
        return _PASSWORD_HASHER.check_needs_rehash(value)
    except InvalidHashError:
        return True
