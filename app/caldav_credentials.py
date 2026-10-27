"""CalDAV credential management separated from calendar collection logic."""
from __future__ import annotations

from pathlib import Path

from .document_store import utc_now
from .service_credentials import ServiceCredentialStore


class CalDAVCredentials(ServiceCredentialStore):
    def __init__(self, auth_path: str | Path, lock: str | Path):
        super().__init__(auth_path, lock)

    def activate(self, username: str, password: str, actor: str) -> str:
        if actor != username or len(password) < 12:
            raise ValueError("CalDAV app password must contain at least 12 characters")
        created_at = utc_now()
        self.replace(username, password, extra={"created_at": created_at})
        return created_at
