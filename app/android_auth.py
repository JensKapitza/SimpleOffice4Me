"""Native local authentication for the embedded Android application.

The Android APK is a local application.  It must never expose the browser login
screen during normal startup.  A short-lived native bootstrap secret is passed
from the Android process to the embedded Python runtime and is accepted only on
loopback.  The selected Android Google account is used as a local identity; no
Google OAuth token is required for this flow.
"""
from __future__ import annotations

import os
import secrets

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, request, url_for

from .access_control import audit
from .auth import _google_username, _login_user, login_required
from .db import get_db
from .password_security import hash_password, verify_password
from .security_controls import clear_login_failures, csrf_token, login_retry_after, record_login_failure


bp = Blueprint("android_auth", __name__, url_prefix="/auth/android")
ANDROID_TOKEN_HEADER = "X-SimpleOffice-Android-Token"
ANDROID_IDENTITY_TABLE = "android_local_account"


def _android_mode() -> bool:
    return os.environ.get("SIMPLEOFFICE_ANDROID", "0").strip().casefold() in {"1", "true", "yes", "on"}


def _android_email() -> str:
    email = os.environ.get("SIMPLEOFFICE_ANDROID_ACCOUNT", "").strip().casefold()
    if not email or len(email) > 320 or "@" not in email or any(character.isspace() for character in email):
        return ""
    return email


def _require_native_request() -> None:
    if not _android_mode():
        abort(404)
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(403, description="Android bootstrap is loopback-only.")
    expected = os.environ.get("SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN", "").strip()
    supplied = request.headers.get(ANDROID_TOKEN_HEADER, "").strip()
    if len(expected) < 32 or len(supplied) < 32 or not secrets.compare_digest(expected, supplied):
        abort(403, description="Android bootstrap token is invalid.")


def _ensure_table(db) -> None:
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS {ANDROID_IDENTITY_TABLE} (
            user_id INTEGER PRIMARY KEY,
            identity TEXT NOT NULL UNIQUE,
            account_email TEXT,
            password_enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES user(id)
        )"""
    )


def _identity_key() -> tuple[str, str]:
    email = _android_email()
    return (f"google:{email}", email) if email else ("local:device", "")


def _default_display_name(email: str) -> str:
    if not email:
        return "Android"
    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
    return local.title() or email


def _android_user(*, create: bool) -> tuple[object | None, object | None]:
    db = get_db()
    _ensure_table(db)
    identity, email = _identity_key()
    linked = db.execute(
        f"""SELECT user.*, android.password_enabled, android.account_email
            FROM {ANDROID_IDENTITY_TABLE} AS android
            JOIN user ON user.id = android.user_id
            WHERE android.identity = ?""",
        (identity,),
    ).fetchone()
    if linked is not None or not create:
        return linked, linked

    user = None
    if email:
        user = db.execute(
            "SELECT * FROM user WHERE email = ? COLLATE NOCASE ORDER BY id LIMIT 1",
            (email,),
        ).fetchone()

    if user is None:
        username = _google_username(db, email or "android@local")
        first_user = db.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 0
        profile_source = "android-google" if email else "android-local"
        db.execute(
            """INSERT INTO user (
                   username, password, display_name, email, profile_source,
                   profile_updated_at, is_admin, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                username,
                hash_password(secrets.token_urlsafe(48)),
                _default_display_name(email),
                email or None,
                profile_source,
                int(first_user),
            ),
        )
        user = db.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
        password_enabled = 0
    else:
        # A manually managed local account keeps its existing password.  Google
        # OAuth accounts historically contain an intentionally unknown random
        # password and therefore start unlocked in the native local app.
        password_enabled = 0 if str(user["profile_source"] or "") in {"google", "android-google"} else 1

    db.execute(
        f"""INSERT INTO {ANDROID_IDENTITY_TABLE}
               (user_id, identity, account_email, password_enabled, updated_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (user["id"], identity, email or None, password_enabled),
    )
    db.commit()
    linked = db.execute(
        f"""SELECT user.*, android.password_enabled, android.account_email
            FROM {ANDROID_IDENTITY_TABLE} AS android
            JOIN user ON user.id = android.user_id
            WHERE android.identity = ?""",
        (identity,),
    ).fetchone()
    return linked, user


def _account_for_user(user_id: int):
    db = get_db()
    _ensure_table(db)
    return db.execute(
        f"SELECT * FROM {ANDROID_IDENTITY_TABLE} WHERE user_id = ?",
        (user_id,),
    ).fetchone()


@bp.app_context_processor
def android_local_auth_context():
    if not _android_mode() or g.get("user") is None:
        return {"android_local_account": None}
    account = _account_for_user(int(g.user["id"]))
    if account is None:
        return {"android_local_account": None}
    return {
        "android_local_account": {
            "email": str(account["account_email"] or ""),
            "password_enabled": bool(account["password_enabled"]),
        }
    }


@bp.get("/challenge")
def challenge():
    _require_native_request()
    return jsonify(ok=True, csrf_token=csrf_token())


@bp.post("/bootstrap")
def bootstrap():
    _require_native_request()
    user, _created_from = _android_user(create=True)
    if user is None:
        return jsonify(ok=False, error="account_unavailable"), 500
    if user["is_disabled"]:
        audit("android_native_login", "session", outcome="denied")
        return jsonify(ok=False, error="account_disabled"), 403
    if bool(user["password_enabled"]):
        return jsonify(
            ok=True,
            password_required=True,
            display_name=str(user["display_name"] or user["username"]),
            email=str(user["account_email"] or ""),
        )
    _login_user(user)
    audit("android_native_login", "session", outcome="success", actor=user)
    return jsonify(
        ok=True,
        password_required=False,
        display_name=str(user["display_name"] or user["username"]),
        email=str(user["account_email"] or ""),
    )


@bp.post("/unlock")
def unlock():
    _require_native_request()
    user, _ = _android_user(create=False)
    if user is None:
        return jsonify(ok=False, error="account_unavailable"), 404
    if user["is_disabled"]:
        return jsonify(ok=False, error="account_disabled"), 403
    if not bool(user["password_enabled"]):
        _login_user(user)
        return jsonify(ok=True)

    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password", ""))
    client_ip = request.remote_addr or "127.0.0.1"
    db = get_db()
    retry_after = login_retry_after(db, str(user["username"]), client_ip)
    if retry_after:
        return jsonify(ok=False, error="throttled", retry_after=retry_after), 429, {"Retry-After": str(retry_after)}
    if not password or not verify_password(str(user["password"]), password):
        record_login_failure(db, str(user["username"]), client_ip)
        audit("android_native_login", "session", outcome="denied")
        return jsonify(ok=False, error="invalid_password"), 401

    clear_login_failures(db, str(user["username"]), client_ip)
    _login_user(user)
    audit("android_native_login", "session", outcome="success", actor=user)
    return jsonify(ok=True)


@bp.post("/password")
@login_required
def password():
    account = _account_for_user(int(g.user["id"]))
    if account is None:
        abort(404)
    action = request.form.get("action", "enable").strip().casefold()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirmation = request.form.get("new_password_confirm", "")
    enabled = bool(account["password_enabled"])
    db = get_db()

    if enabled and not verify_password(str(g.user["password"]), current_password):
        flash("Das aktuelle App-Passwort ist falsch.")
        return redirect(url_for("documents.settings"))

    if action == "disable":
        db.execute(
            "UPDATE user SET password = ?, auth_version = auth_version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (hash_password(secrets.token_urlsafe(48)), g.user["id"]),
        )
        db.execute(
            f"UPDATE {ANDROID_IDENTITY_TABLE} SET password_enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (g.user["id"],),
        )
        db.commit()
        refreshed = db.execute("SELECT * FROM user WHERE id = ?", (g.user["id"],)).fetchone()
        _login_user(refreshed)
        audit("android_password", "user", str(g.user["id"]), outcome="disabled", actor=refreshed)
        flash("App-Passwortschutz deaktiviert. SimpleOffice startet wieder direkt.")
        return redirect(url_for("documents.settings"))

    if action not in {"enable", "change"}:
        abort(400)
    if len(new_password) < 12:
        flash("Das App-Passwort muss mindestens 12 Zeichen haben.")
        return redirect(url_for("documents.settings"))
    if len(new_password) > 128:
        flash("Das App-Passwort darf höchstens 128 Zeichen haben.")
        return redirect(url_for("documents.settings"))
    if new_password != confirmation:
        flash("Die beiden neuen Passwörter stimmen nicht überein.")
        return redirect(url_for("documents.settings"))

    db.execute(
        "UPDATE user SET password = ?, auth_version = auth_version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (hash_password(new_password), g.user["id"]),
    )
    db.execute(
        f"UPDATE {ANDROID_IDENTITY_TABLE} SET password_enabled = 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (g.user["id"],),
    )
    db.commit()
    refreshed = db.execute("SELECT * FROM user WHERE id = ?", (g.user["id"],)).fetchone()
    _login_user(refreshed)
    audit("android_password", "user", str(g.user["id"]), outcome="enabled" if not enabled else "changed", actor=refreshed)
    flash("App-Passwortschutz aktiviert." if not enabled else "App-Passwort wurde geändert.")
    return redirect(url_for("documents.settings"))
