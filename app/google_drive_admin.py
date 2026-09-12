"""User-facing Google Drive synchronization controls."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .access_control import audit, has_feature
from .auth import GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, _google_config, _google_json_request, login_required
from .db import get_db
from .google_drive_schema import ensure_google_drive_schema
from .google_drive_sync import (
    LOCAL_SYNC_FOLDER,
    VALID_DIRECTIONS,
    drive_links,
    ensure_drive_state,
    sync_google_drive,
    update_drive_settings,
)
from .google_tokens import GOOGLE_DRIVE_SCOPE, google_scopes
from .security_controls import protect_value, unprotect_value

bp = Blueprint("google_drive_admin", __name__, url_prefix="/settings/google-drive")


def drive_access_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if not has_feature(g.user, "documents") or not has_feature(g.user, "sync"):
            abort(403, description="Google Drive sync is disabled for this account")
        return view(**kwargs)
    return wrapped_view


def _connection_status(user_id: int) -> tuple[bool, bool, set[str]]:
    row = get_db().execute(
        "SELECT 1 FROM oauth_token WHERE provider='google' AND user_id=?",
        (int(user_id),),
    ).fetchone()
    scopes = google_scopes(user_id) if row else set()
    return row is not None, GOOGLE_DRIVE_SCOPE in scopes, scopes


def _store_drive_token(user_id: int, token: dict, requested_scopes: str) -> None:
    access_token = str(token.get("access_token", "")).strip()
    if not access_token:
        raise ValueError("Google did not return an access token")
    db = get_db()
    previous = db.execute(
        "SELECT refresh_token, scopes FROM oauth_token WHERE provider='google' AND user_id=?",
        (int(user_id),),
    ).fetchone()
    previous_refresh = ""
    previous_scopes = ""
    if previous:
        previous_refresh = unprotect_value(str(previous["refresh_token"] or ""), "google-oauth")
        previous_scopes = str(previous["scopes"] or "")
    refresh_token = str(token.get("refresh_token", "")).strip() or previous_refresh
    scopes = str(token.get("scope", "")).strip() or requested_scopes or previous_scopes
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=max(60, int(token.get("expires_in", 3600) or 3600)))
    ).replace(microsecond=0).isoformat()
    db.execute(
        """INSERT INTO oauth_token(
               provider, user_id, access_token, refresh_token, expires_at, scopes, updated_at
           ) VALUES ('google', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(provider, user_id) DO UPDATE SET
               access_token=excluded.access_token,
               refresh_token=excluded.refresh_token,
               expires_at=excluded.expires_at,
               scopes=excluded.scopes,
               updated_at=CURRENT_TIMESTAMP""",
        (
            int(user_id),
            protect_value(access_token, "google-oauth"),
            protect_value(refresh_token, "google-oauth") if refresh_token else "",
            expires_at,
            scopes,
        ),
    )
    db.commit()


def _finish_drive_oauth_callback():
    current_user = getattr(g, "user", None)
    if current_user is None:
        session.pop("google_drive_oauth_pending", None)
        flash("Die Drive-Freigabe benötigt ein angemeldetes SimpleOffice-Konto.", "warning")
        return redirect(url_for("auth.login"))
    if request.args.get("error"):
        session.pop("google_drive_oauth_pending", None)
        session.pop("google_oauth_state", None)
        flash("Google-Drive-Freigabe wurde abgebrochen.", "warning")
        return redirect(url_for("google_drive_admin.index"))
    expected_state = str(session.pop("google_oauth_state", ""))
    received_state = str(request.args.get("state", ""))
    code = str(request.args.get("code", ""))
    config = _google_config()
    if (
        config is None
        or not code
        or not expected_state
        or not secrets.compare_digest(expected_state, received_state)
    ):
        session.pop("google_drive_oauth_pending", None)
        flash("Google-Drive-Freigabe konnte nicht sicher geprüft werden.", "danger")
        return redirect(url_for("google_drive_admin.index"))
    requested_scopes = str(session.pop("google_drive_requested_scopes", ""))
    try:
        body = urlencode(
            {
                "code": code,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": config["redirect_uri"],
                "grant_type": "authorization_code",
            }
        ).encode("utf-8")
        token = _google_json_request(
            GOOGLE_TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        _store_drive_token(current_user["id"], token, requested_scopes)
    except Exception as exc:
        current_app.logger.exception("Google Drive OAuth callback failed")
        audit(
            "google_drive_connect_failed", "integration", "google-drive", outcome="failure",
            detail={"error_type": type(exc).__name__},
        )
        session.pop("google_drive_oauth_pending", None)
        flash("Google Drive konnte nicht verbunden werden.", "danger")
        return redirect(url_for("google_drive_admin.index"))
    session.pop("google_drive_oauth_pending", None)
    audit("google_drive_connected", "integration", "google-drive")
    flash("Google Drive wurde für dieses SimpleOffice-Konto freigegeben.", "success")
    return redirect(url_for("google_drive_admin.index"))


@bp.before_app_request
def intercept_google_drive_consent():
    if request.endpoint == "auth.google_callback" and session.get("google_drive_oauth_pending"):
        return _finish_drive_oauth_callback()
    return None


@bp.get("")
@drive_access_required
def index():
    ensure_google_drive_schema()
    state = ensure_drive_state(g.user["id"])
    connected, drive_allowed, scopes = _connection_status(g.user["id"])
    links = drive_links(g.user["id"], 200)
    counts: dict[str, int] = {}
    for item in links:
        status = str(item.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1
    return render_template(
        "google_drive/index.html",
        drive_state=state,
        drive_links=links,
        drive_counts=counts,
        google_connected=connected,
        drive_allowed=drive_allowed,
        google_scopes=sorted(scopes),
        google_configured=bool(
            current_app.config.get("GOOGLE_OAUTH_CLIENT_ID")
            and current_app.config.get("GOOGLE_OAUTH_CLIENT_SECRET")
        ),
        local_sync_folder=LOCAL_SYNC_FOLDER,
    )


@bp.post("/connect")
@drive_access_required
def connect():
    config = _google_config()
    if config is None:
        flash("Google OAuth ist noch nicht konfiguriert.", "warning")
        return redirect(url_for("google_drive_admin.index"))
    state = secrets.token_urlsafe(32)
    scopes = " ".join(
        (
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
            GOOGLE_DRIVE_SCOPE,
        )
    )
    session["google_oauth_state"] = state
    session["google_drive_oauth_pending"] = 1
    session["google_drive_requested_scopes"] = scopes
    parameters = {
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "prompt": "consent select_account",
        "access_type": "offline",
        "include_granted_scopes": "true",
    }
    audit("google_drive_connect_started", "integration", "google-drive")
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(parameters)}")


@bp.post("/settings")
@drive_access_required
def save_settings():
    direction = request.form.get("direction", "bidirectional").strip()
    enabled = request.form.get("enabled") == "1"
    if direction not in VALID_DIRECTIONS:
        abort(400, description="Invalid Google Drive sync direction")
    update_drive_settings(g.user["id"], direction=direction, enabled=enabled)
    audit(
        "google_drive_settings_updated", "integration", "google-drive",
        detail={"direction": direction, "enabled": enabled},
    )
    flash("Google-Drive-Synchronisation wurde gespeichert.", "success")
    return redirect(url_for("google_drive_admin.index"))


@bp.post("/sync")
@drive_access_required
def sync_now():
    ensure_google_drive_schema()
    try:
        result = sync_google_drive(g.user["id"], str(g.user["username"]))
        audit(
            "google_drive_sync", "integration", "google-drive",
            detail={key: value for key, value in result.items() if key != "status"},
        )
        if result.get("status") == "disabled":
            flash("Google-Drive-Synchronisation ist deaktiviert.", "info")
        else:
            flash(
                "Google Drive abgeglichen: "
                f"{result.get('uploaded', 0)} hochgeladen, "
                f"{result.get('downloaded', 0)} heruntergeladen, "
                f"{result.get('conflicts', 0)} Konflikte.",
                "success",
            )
    except PermissionError as exc:
        audit("google_drive_sync", "integration", "google-drive", outcome="denied")
        flash(f"Google Drive muss erneut freigegeben werden: {exc}", "warning")
    except Exception as exc:
        current_app.logger.exception("Google Drive sync failed")
        audit(
            "google_drive_sync", "integration", "google-drive", outcome="failure",
            detail={"error_type": type(exc).__name__},
        )
        flash(f"Google-Drive-Synchronisation fehlgeschlagen: {type(exc).__name__}", "danger")
    return redirect(url_for("google_drive_admin.index"))


@bp.post("/full-rescan")
@drive_access_required
def full_rescan():
    ensure_google_drive_schema()
    db = get_db()
    db.execute(
        """UPDATE google_drive_state SET page_token=NULL, last_error='', updated_at=CURRENT_TIMESTAMP
           WHERE user_id=?""",
        (int(g.user["id"]),),
    )
    db.commit()
    audit("google_drive_full_rescan_requested", "integration", "google-drive")
    flash("Beim nächsten Sync wird der verwaltete Google-Drive-Ordner vollständig neu abgeglichen.", "info")
    return redirect(url_for("google_drive_admin.index"))
