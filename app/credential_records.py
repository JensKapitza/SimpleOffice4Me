"""Credential migration helpers for file-backed service accounts.

New credentials are always Argon2id. Historical raw-scrypt records are accepted
only long enough to authenticate once and can then be rewritten by the owning
store using :func:`upgraded_password_record`.
"""
from __future__ import annotations

from .password_security import hash_password, password_needs_rehash, verify_password_record


def new_password_record(password: str, *, hash_key: str = "password_hash", salt_key: str = "password_salt") -> dict[str, str]:
    """Create a new Argon2id credential record while preserving legacy schema keys."""
    return {hash_key: hash_password(password), salt_key: ""}


def credential_matches(record: dict, password: str, *, hash_key: str = "password_hash", salt_key: str = "password_salt") -> bool:
    """Verify current Argon2id or migration-only legacy raw-scrypt credentials."""
    return verify_password_record(record, password, hash_key=hash_key, salt_key=salt_key)


def credential_needs_upgrade(record: dict, *, hash_key: str = "password_hash") -> bool:
    """Return whether a successfully authenticated record should be rewritten."""
    return password_needs_rehash(str(record.get(hash_key, "")))


def upgraded_password_record(password: str, *, hash_key: str = "password_hash", salt_key: str = "password_salt") -> dict[str, str]:
    """Return replacement Argon2id fields after successful legacy authentication."""
    return new_password_record(password, hash_key=hash_key, salt_key=salt_key)
