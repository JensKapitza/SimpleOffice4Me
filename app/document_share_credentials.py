"""Password protection helpers for document shares."""
from __future__ import annotations

from .credential_records import (
    credential_matches,
    credential_needs_upgrade,
    new_password_record,
    upgraded_password_record,
)


def new_share_password_record(password: str) -> dict[str, str]:
    return new_password_record(password)


def share_password_matches(share: dict, password: str) -> bool:
    return credential_matches(share, password)


def share_password_needs_upgrade(share: dict) -> bool:
    return credential_needs_upgrade(share)


def upgraded_share_password_record(password: str) -> dict[str, str]:
    return upgraded_password_record(password)
