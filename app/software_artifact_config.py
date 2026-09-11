"""Admin-managed GitHub Actions artifact configuration.

The repository is stored as ordinary configuration.  The access token is
purpose-encrypted with the application's installation secret and is never
returned to templates or audit payloads.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write
from .file_lock import exclusive_file_lock
from .security_controls import protect_value, unprotect_value
from .software_artifacts import SoftwareArtifactStore

CONFIG_SCHEMA = 1
DEFAULT_GITHUB_REPOSITORY = "JensKapitza/SimpleOffice4Me"
_TOKEN_PURPOSE = "software.github-artifact-token"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def normalize_github_repository(value: str) -> str:
    """Accept owner/repo or a github.com repository URL and return owner/repo."""
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_GITHUB_REPOSITORY

    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com", "api.github.com"}:
            raise ValueError("Es sind nur HTTPS-Repository-URLs von github.com erlaubt")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("GitHub-Repository-URL enthält nicht erlaubte Bestandteile")
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if parsed.hostname == "api.github.com":
            if len(parts) != 3 or parts[0] != "repos":
                raise ValueError("Ungültige GitHub-Repository-URL")
            raw = "/".join(parts[1:])
        else:
            if len(parts) != 2:
                raise ValueError("GitHub-URL muss direkt auf ein Repository zeigen")
            raw = "/".join(parts)

    raw = raw.removesuffix(".git").strip("/")
    if not _REPOSITORY_RE.fullmatch(raw):
        raise ValueError("GitHub-Repository muss als owner/repository oder vollständige HTTPS-URL angegeben werden")
    return raw


class SoftwareArtifactConfiguration:
    def __init__(self, document_root: str | Path):
        root = Path(document_root).expanduser().resolve()
        self.base = root / CONTROL_DIR / "software-distribution"
        self.path = self.base / "github-artifacts.json"
        self.lock_path = self.base / ".github-artifacts-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    @staticmethod
    def _legacy_repository() -> str:
        return normalize_github_repository(
            os.environ.get("SIMPLEOFFICE_GITHUB_ARTIFACT_REPOSITORY", DEFAULT_GITHUB_REPOSITORY)
        )

    @staticmethod
    def _legacy_token() -> str:
        token_file = os.environ.get("SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN_FILE", "").strip()
        if token_file:
            path = Path(token_file).expanduser()
            try:
                if path.is_file() and not path.is_symlink():
                    token = path.read_text(encoding="utf-8").strip()
                    if token:
                        return token
            except OSError:
                pass
        return os.environ.get("SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN", "").strip()

    def is_managed(self) -> bool:
        return bool(self._read().get("managed") is True)

    def repository(self) -> str:
        state = self._read()
        if state.get("managed") is True:
            return normalize_github_repository(str(state.get("repository") or DEFAULT_GITHUB_REPOSITORY))
        return self._legacy_repository()

    def token(self) -> str:
        state = self._read()
        if state.get("managed") is True:
            protected = str(state.get("token") or "")
            return unprotect_value(protected, _TOKEN_PURPOSE).strip() if protected else ""
        return self._legacy_token()

    def info(self) -> dict[str, Any]:
        state = self._read()
        managed = state.get("managed") is True
        repository = self.repository()
        configured = bool(self.token())
        if managed:
            source = "admin"
        elif self._legacy_token() or os.environ.get("SIMPLEOFFICE_GITHUB_ARTIFACT_REPOSITORY", "").strip():
            source = "environment"
        else:
            source = "default"
        return {
            "configured": configured,
            "repository": repository,
            "repository_url": f"https://github.com/{repository}",
            "source": source,
            "managed": managed,
            "updated_at": int(state.get("updated_at") or 0) if managed else 0,
        }

    def save(self, repository: str, *, token: str = "", clear_token: bool = False, actor: str = "") -> dict[str, Any]:
        normalized_repository = normalize_github_repository(repository)
        supplied_token = str(token or "").strip()
        if clear_token and supplied_token:
            raise ValueError("Token kann nicht gleichzeitig ersetzt und entfernt werden")

        previous = self._read()
        previous_managed = previous.get("managed") is True
        if clear_token:
            protected_token = ""
        elif supplied_token:
            protected_token = protect_value(supplied_token, _TOKEN_PURPOSE)
        elif previous_managed:
            protected_token = str(previous.get("token") or "")
        else:
            legacy = self._legacy_token()
            protected_token = protect_value(legacy, _TOKEN_PURPOSE) if legacy else ""

        state = {
            "schema": CONFIG_SCHEMA,
            "managed": True,
            "repository": normalized_repository,
            "token": protected_token,
            "updated_at": int(time.time()),
            "updated_by": str(actor or "")[:200],
        }
        self.base.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            atomic_json_write(self.path, state)
        return self.info()


class ConfiguredSoftwareArtifactStore(SoftwareArtifactStore):
    """Software artifact store whose GitHub source is controlled by Admin settings."""

    def __init__(self, document_root: str | Path):
        super().__init__(document_root)
        self.github_configuration = SoftwareArtifactConfiguration(document_root)

    def _github_repository(self) -> str:
        return self.github_configuration.repository()

    def _github_token(self) -> str:
        return self.github_configuration.token()

    def github_sync_info(self) -> dict[str, Any]:
        return self.github_configuration.info()

    def sync_from_github(self) -> dict[str, Any]:
        if not self._github_token():
            raise ValueError("GitHub-Artefakt-Sync benötigt einen Token in Administration → Einstellungen")
        return super().sync_from_github()
