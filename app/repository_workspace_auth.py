"""Optional, visitor-scoped GitHub App authentication for repository reads."""
from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from flask import current_app, request, session

from . import app_config_store
from . import repository_workspace_oauth as oauth
from . import repository_workspace_store as store
from .repository_workspace_github import GitHubReadClient, WorkspaceError
from .repository_workspace_store import WorkspaceAuthError

SESSION_KEY = "github_workspace_capability"
CLIENT_ID_RE = re.compile(r"Iv[0-9]+\.[A-Za-z0-9]{6,96}\Z")
CAPABILITY_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
MAX_SESSION_SECONDS = 8 * 60 * 60
REPOSITORY_ID = "368332171"


def _configured_client_id() -> str:
    configured = current_app.config.get("GITHUB_WORKSPACE_CLIENT_ID")
    if configured is None:
        configured = app_config_store.get("repository_workspace", "github_client_id", "")
        configured = configured or os.environ.get("SIMPLEOFFICE_GITHUB_WORKSPACE_CLIENT_ID", "")
    value = str(configured or "").strip()
    return value if CLIENT_ID_RE.fullmatch(value) else ""


def _loopback_host(value: str) -> bool:
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def secure_transport() -> bool:
    if request.is_secure:
        return True
    try:
        host = urlsplit(request.host_url).hostname or ""
        return _loopback_host(host) and _loopback_host(request.remote_addr or "")
    except ValueError:
        return False


def _require_auth_available() -> str:
    client_id = _configured_client_id()
    if not client_id:
        raise WorkspaceAuthError("Eine GitHub App mit aktiviertem Device Flow ist noch nicht konfiguriert.", 503)
    if not secure_transport():
        raise WorkspaceAuthError("Die GitHub-Anmeldung benötigt HTTPS oder einen lokalen Loopback-Zugriff.", 403)
    return client_id


def _key(*, create: bool = False) -> str:
    value = session.get(SESSION_KEY, "")
    if not isinstance(value, str) or not CAPABILITY_RE.fullmatch(value):
        if not create:
            return ""
        value = secrets.token_urlsafe(32)
        session[SESSION_KEY] = value
    return store.session_key(value)


def _row() -> dict | None:
    key = _key()
    row = store.read(key) if key and secure_transport() else None
    if row and row["client_id"] != _configured_client_id():
        store.discard(key)
        session.pop(SESSION_KEY, None)
        return None
    return row


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def status() -> dict:
    client_id = _configured_client_id()
    secure = secure_transport()
    row = _row()
    connected = bool(row and row["state"] == "authenticated")
    reason = ""
    if not client_id:
        reason = "Öffentlich lesbar. Für die optionale Anmeldung ist noch keine GitHub App konfiguriert."
    elif not secure:
        reason = "Öffentlich lesbar. Die optionale GitHub-Anmeldung benötigt HTTPS oder lokalen Loopback-Zugriff."
    return {
        "enabled": bool(client_id), "authenticated": connected,
        "login": row["login"] if connected else None,
        "expires_at": _iso(row["expires_at"]) if connected else None,
        "can_authenticate": bool(client_id and secure), "reason": reason,
        "context_id": row["context_id"] if row else "public",
    }


def cache_key() -> str:
    row = _row()
    return _cache_key(row)


def _cache_key(row: dict | None) -> str:
    if row and row["state"] == "authenticated":
        return "private:" + row["session_key"] + ":" + row["context_id"]
    return "public"


def credentials() -> tuple[str, str]:
    row = _row()
    if not row or row["state"] != "authenticated":
        return "", "public"
    try:
        token = store.unprotect(row).get("access_token", "")
        if not _valid_token(token):
            raise WorkspaceAuthError("Die GitHub-Sitzung ist ungültig. Bitte erneut anmelden.", 401)
    except WorkspaceAuthError:
        store.discard(row["session_key"])
        session.pop(SESSION_KEY, None)
        raise
    return token, _cache_key(row)


def disconnect() -> str:
    key = _key()
    row = store.revoke(key)
    previous_cache_key = _cache_key(row)
    session.pop(SESSION_KEY, None)
    return previous_cache_key


def _bounded_number(value, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        raise ValueError("Expected integer")
    number = int(value)
    if number < lower or number > upper:
        raise ValueError("Integer out of range")
    return number


def _pending_payload(row: dict) -> dict:
    payload = store.unprotect(row)
    return {"status": "pending", "user_code": payload["user_code"],
            "verification_uri": "https://github.com/login/device",
            "expires_at": _iso(row["expires_at"]), "interval": row["interval"]}


def start() -> dict:
    client_id = _require_auth_available()
    key = _key(create=True)
    row = store.reserve_start(key, client_id, request.remote_addr or "")
    if row["state"] == "pending":
        return _pending_payload(row)
    if row["state"] == "authenticated":
        return {"status": "authenticated"}
    try:
        payload = oauth.post(oauth.DEVICE_URL, {"client_id": client_id, "scope": "offline_access"})
        if payload.get("error"):
            raise WorkspaceAuthError("GitHub Device Flow ist für diese App nicht verfügbar. Konfiguration prüfen.", 502)
        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        uri = payload.get("verification_uri")
        if (not isinstance(device_code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", device_code)
                or not isinstance(user_code, str) or not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", user_code)
                or uri != "https://github.com/login/device"):
            raise ValueError("Invalid device response")
        interval = _bounded_number(payload.get("interval", 5), 1, 60)
        now = int(time.time())
        expires_at = now + _bounded_number(payload.get("expires_in"), 1, 1800)
        protected = {"device_code": device_code, "user_code": user_code}
        if not store.finish_start(row, protected, expires_at, interval, now=now):
            raise WorkspaceAuthError("Die Anmeldung wurde abgebrochen oder ist abgelaufen.", 409)
        return {"status": "pending", "user_code": user_code, "verification_uri": uri,
                "expires_at": _iso(expires_at), "interval": interval}
    except (ValueError, TypeError, OverflowError):
        store.discard(key, row["context_id"])
        raise WorkspaceAuthError("GitHub hat eine ungültige Anmeldeantwort geliefert.", 502) from None
    except WorkspaceAuthError:
        store.discard(key, row["context_id"])
        raise


def _valid_token(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"ghu_[A-Za-z0-9_]{20,4090}", value))


def _finish_authorization(row: dict, payload: dict) -> dict:
    token = payload.get("access_token", "")
    if not _valid_token(token) or str(payload.get("token_type", "")).casefold() != "bearer":
        raise WorkspaceAuthError("GitHub hat keinen gültigen GitHub-App-Token geliefert.", 502)
    try:
        lifetime = _bounded_number(payload.get("expires_in", MAX_SESSION_SECONDS), 1, 365 * 86400)
        expires_at = int(time.time()) + max(1, min(lifetime, MAX_SESSION_SECONDS) - 30)
        profile = GitHubReadClient(token).get("user")
        login = profile.get("login") if isinstance(profile, dict) else None
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
            raise ValueError("Invalid GitHub identity")
    except (ValueError, TypeError, OverflowError):
        raise WorkspaceAuthError("GitHub hat eine ungültige Kontoantwort geliefert.", 502) from None
    except WorkspaceError:
        raise WorkspaceAuthError("Das GitHub-Konto konnte nicht geprüft werden. Bitte erneut anmelden.", 502) from None
    new_capability = secrets.token_urlsafe(32)
    if row["client_id"] != _configured_client_id() or not store.finish_poll(
            row, token=token, login=login, expires_at=expires_at, new_key=store.session_key(new_capability)):
        return {"status": "expired", "message": "Die Anmeldung wurde abgebrochen oder ist abgelaufen."}
    session[SESSION_KEY] = new_capability
    return {"status": "authenticated"}


def poll() -> dict:
    client_id = _require_auth_available()
    key = _key()
    row, reserved = store.reserve_poll(key) if key else (None, False)
    if row is None or row["client_id"] != client_id:
        return {"status": "expired", "message": "Die Anmeldung ist abgelaufen. Bitte erneut beginnen."}
    if row["state"] == "authenticated":
        return {"status": "authenticated"}
    if not reserved:
        remaining = max(row["poll_after"], row["lease_until"]) - int(time.time())
        return {"status": "pending", "interval": max(1, remaining, row["interval"])}
    try:
        secret = store.unprotect(row)
        payload = oauth.post(oauth.TOKEN_URL, {
            "client_id": client_id, "device_code": secret["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "repository_id": REPOSITORY_ID,
        })
        error = payload.get("error", "")
        if error in {"authorization_pending", "slow_down"}:
            interval = row["interval"] + (5 if error == "slow_down" else 0)
            if payload.get("interval") is not None:
                interval = max(interval, _bounded_number(payload["interval"], 1, 1800))
            if not store.finish_poll(row, interval=interval):
                return {"status": "expired"}
            return {"status": "slow_down" if error == "slow_down" else "pending", "interval": interval}
        if error in {"access_denied", "expired_token"}:
            store.discard(key, row["context_id"])
            return {"status": "denied" if error == "access_denied" else "expired"}
        if error:
            raise WorkspaceAuthError("GitHub hat die Anmeldung abgelehnt. Bitte erneut beginnen.", 502)
        return _finish_authorization(row, payload)
    except WorkspaceAuthError:
        store.discard(key, row["context_id"])
        raise
    except (KeyError, ValueError, TypeError):
        store.discard(key, row["context_id"])
        raise WorkspaceAuthError("Die GitHub-Anmeldung ist ungültig. Bitte erneut beginnen.", 502) from None
