"""User-facing Google Drive synchronization controls."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .access_control import audit, has_feature
from .auth import login_required
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
    session["google_oauth_return_to"] = url_for("google_drive_admin.index")
    audit("google_drive_connect_started", "integration", "google-drive")
    return redirect(url_for("auth.google_login", drive="1"))


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
    get_db().execute(
        """UPDATE google_drive_state SET page_token=NULL, last_error='', updated_at=CURRENT_TIMESTAMP
           WHERE user_id=?""",
        (int(g.user["id"]),),
    )
    get_db().commit()
    audit("google_drive_full_rescan_requested", "integration", "google-drive")
    flash("Beim nächsten Sync wird der verwaltete Google-Drive-Ordner vollständig neu abgeglichen.", "info")
    return redirect(url_for("google_drive_admin.index"))
