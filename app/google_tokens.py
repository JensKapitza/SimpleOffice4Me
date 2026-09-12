"""Reusable encrypted Google OAuth token access and refresh helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import current_app

from .auth import GOOGLE_TOKEN_URL, _google_json_request
from .db import get_db
from .security_controls import protect_value, unprotect_value

GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)


def _parse_expiry(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def google_scopes(user_id: int) -> set[str]:
    row = get_db().execute(
        "SELECT scopes FROM oauth_token WHERE provider='google' AND user_id=?",
        (int(user_id),),
    ).fetchone()
    if row is None:
        return set()
    return {item for item in str(row["scopes"] or "").split() if item}


def google_access_token(
    user_id: int,
    *,
    required_scope: str = "",
    force_refresh: bool = False,
) -> str:
    """Return a valid Google access token, refreshing it when needed.

    OAuth material remains encrypted in SQLite.  Callers never need to know the
    stored refresh token and may require a scope before any network operation.
    """
    db = get_db()
    row = db.execute(
        """SELECT access_token, refresh_token, expires_at, scopes
           FROM oauth_token WHERE provider='google' AND user_id=?""",
        (int(user_id),),
    ).fetchone()
    if row is None:
        raise PermissionError("Google account is not connected")

    scopes = {item for item in str(row["scopes"] or "").split() if item}
    if required_scope and required_scope not in scopes:
        raise PermissionError("Google permission is missing")

    access_token = unprotect_value(str(row["access_token"] or ""), "google-oauth")
    refresh_token = unprotect_value(str(row["refresh_token"] or ""), "google-oauth")
    expires_at = _parse_expiry(str(row["expires_at"] or ""))
    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and access_token
        and expires_at is not None
        and expires_at > now + TOKEN_REFRESH_MARGIN
    ):
        return access_token

    client_id = str(current_app.config.get("GOOGLE_OAUTH_CLIENT_ID", "")).strip()
    client_secret = str(current_app.config.get("GOOGLE_OAUTH_CLIENT_SECRET", "")).strip()
    if not client_id or not client_secret or not refresh_token:
        raise PermissionError("Google account must be connected again")

    body = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    payload = _google_json_request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    new_access = str(payload.get("access_token", "")).strip()
    if not new_access:
        raise RuntimeError("Google did not return a refreshed access token")
    expires_in = max(60, int(payload.get("expires_in", 3600) or 3600))
    new_expiry = (now + timedelta(seconds=expires_in)).replace(microsecond=0).isoformat()
    new_refresh = str(payload.get("refresh_token", "")).strip() or refresh_token
    new_scopes = str(payload.get("scope", "")).strip() or str(row["scopes"] or "")
    db.execute(
        """UPDATE oauth_token
           SET access_token=?, refresh_token=?, expires_at=?, scopes=?, updated_at=CURRENT_TIMESTAMP
           WHERE provider='google' AND user_id=?""",
        (
            protect_value(new_access, "google-oauth"),
            protect_value(new_refresh, "google-oauth"),
            new_expiry,
            new_scopes,
            int(user_id),
        ),
    )
    db.commit()
    return new_access
