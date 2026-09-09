"""WebDAV app-password hashing and migration helpers.

HTTP Digest algorithms are intentionally not handled here; this module only
protects stored WebDAV app credentials.
"""
from __future__ import annotations

from .credential_records import (
    credential_matches,
    credential_needs_upgrade,
    new_password_record,
    upgraded_password_record,
)


def new_webdav_password_record(password: str) -> dict[str, str]:
    return new_password_record(password, hash_key="hash", salt_key="salt")


def webdav_password_matches(record: dict, password: str) -> bool:
    return credential_matches(record, password, hash_key="hash", salt_key="salt")


def webdav_password_needs_upgrade(record: dict) -> bool:
    return credential_needs_upgrade(record, hash_key="hash")


def upgraded_webdav_password_record(password: str) -> dict[str, str]:
    return upgraded_password_record(password, hash_key="hash", salt_key="salt")
