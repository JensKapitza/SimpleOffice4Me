"""Small, reusable store for file-backed service credentials.

This module keeps password hashing and legacy migration out of protocol/store
modules. New credentials use Argon2id. Historical raw-scrypt records are only
accepted for migration after successful authentication.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .credential_records import (
    credential_matches,
    credential_needs_upgrade,
    new_password_record,
    upgraded_password_record,
)
from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


class ServiceCredentialStore:
    """Manage one JSON credential file with atomic, locked migration."""

    def __init__(self, path: str | Path, lock: str | Path, *, accounts_key: str = "accounts"):
        self.path = Path(path)
        self.lock = Path(lock)
        self.accounts_key = accounts_key

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {self.accounts_key: []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {self.accounts_key: []}
        if not isinstance(data, dict) or not isinstance(data.get(self.accounts_key, []), list):
            return {self.accounts_key: []}
        data.setdefault(self.accounts_key, [])
        return data

    def replace(self, username: str, password: str, *, extra: dict[str, Any] | None = None,
                hash_key: str = "password_hash", salt_key: str = "password_salt") -> dict[str, Any]:
        record = {
            "username": username,
            "enabled": True,
            "created_at": utc_now(),
            **new_password_record(password, hash_key=hash_key, salt_key=salt_key),
            **(extra or {}),
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            records = data[self.accounts_key]
            data[self.accounts_key] = [item for item in records if item.get("username") != username] + [record]
            atomic_json_write(self.path, data)
        return record

    def authenticate(self, username: str, password: str, *, hash_key: str = "password_hash",
                     salt_key: str = "password_salt") -> bool:
        data = self._read()
        record = next((item for item in data[self.accounts_key]
                       if item.get("username") == username and item.get("enabled") is True), None)
        if record is None or not credential_matches(record, password, hash_key=hash_key, salt_key=salt_key):
            return False
        if credential_needs_upgrade(record, hash_key=hash_key):
            self._upgrade(username, password, hash_key=hash_key, salt_key=salt_key)
        return True

    def _upgrade(self, username: str, password: str, *, hash_key: str, salt_key: str) -> None:
        """Re-read and re-verify under lock before changing a credential."""
        with exclusive_file_lock(self.lock):
            data = self._read()
            current = next((item for item in data[self.accounts_key]
                            if item.get("username") == username and item.get("enabled") is True), None)
            if current is None:
                return
            if not credential_matches(current, password, hash_key=hash_key, salt_key=salt_key):
                return
            if not credential_needs_upgrade(current, hash_key=hash_key):
                return
            current.update(upgraded_password_record(password, hash_key=hash_key, salt_key=salt_key))
            atomic_json_write(self.path, data)
