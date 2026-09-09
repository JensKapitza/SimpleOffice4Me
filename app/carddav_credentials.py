"""CardDAV credential management separated from the contact store."""
from __future__ import annotations

from pathlib import Path

from .service_credentials import ServiceCredentialStore


class CardDAVCredentials(ServiceCredentialStore):
    def __init__(self, auth_path: str | Path, lock: str | Path):
        super().__init__(auth_path, lock)

    def activate(self, username: str, password: str) -> dict:
        if len(password) < 12:
            raise ValueError("CardDAV app password must contain at least 12 characters")
        return self.replace(username, password)
