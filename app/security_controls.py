"""Browser-request and authentication safeguards.

The DAV and MCP protocols use independently scoped credentials and are not
cookie-authenticated browser endpoints.  They are deliberately excluded from
the synchronizer-token check below.
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import secrets
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from flask import abort, current_app, request, session


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_BLOCK_SECONDS = 15 * 60
LOGIN_LIMITS = {"account": 5, "network": 25}


def csrf_token() -> str:
    """Return the per-session synchronizer token, creating it when needed."""
    token = session.get("_csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def rotate_csrf_token() -> None:
    session["_csrf_token"] = secrets.token_urlsafe(32)


def protect_value(value: str, purpose: str) -> str:
    """Encrypt an application secret with purpose-separated authenticated encryption."""
    if not value:
        return ""
    aad = f"simpleoffice:{purpose}:v1".encode("utf-8")
    key = hashlib.sha256(aad + b"\0" + str(current_app.config["SECRET_KEY"]).encode("utf-8")).digest()
    nonce = secrets.token_bytes(12)
    payload = nonce + AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad)
    return "enc:v1:" + base64.urlsafe_b64encode(payload).decode("ascii")


def unprotect_value(value: str, purpose: str) -> str:
    """Decrypt a protected value; accept legacy plaintext for one-time migration."""
    if not value or not value.startswith("enc:v1:"):
        return value
    aad = f"simpleoffice:{purpose}:v1".encode("utf-8")
    key = hashlib.sha256(aad + b"\0" + str(current_app.config["SECRET_KEY"]).encode("utf-8")).digest()
    raw = base64.urlsafe_b64decode(value.removeprefix("enc:v1:").encode("ascii"))
    return AESGCM(key).decrypt(raw[:12], raw[12:], aad).decode("utf-8")


def protect_browser_mutation() -> None:
    """Reject forged state-changing requests to browser endpoints."""
    if request.method not in UNSAFE_METHODS:
        return
    if current_app.testing and not current_app.config.get("TEST_CSRF_PROTECTION", False):
        return
    # Only credentialed protocol resources are exempt. Their blueprints also
    # contain browser settings pages, which must remain CSRF protected.
    if request.path.startswith(("/caldav/", "/carddav/", "/webdav/")) or request.path == "/mcp":
        return
    expected = session.get("_csrf_token")
    supplied = request.form.get("_csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not isinstance(expected, str) or not isinstance(supplied, str):
        abort(403, description="Die Sicherheitsprüfung der Anfrage ist fehlgeschlagen. Bitte die Seite neu laden.")
    if not secrets.compare_digest(expected, supplied):
        abort(403, description="Die Sicherheitsprüfung der Anfrage ist fehlgeschlagen. Bitte die Seite neu laden.")


def _login_key(scope: str, username: str, client_ip: str) -> str:
    material = client_ip if scope == "network" else f"{client_ip}\0{username.casefold()}"
    digest = hmac.new(
        str(current_app.config["SECRET_KEY"]).encode("utf-8"),
        material.encode("utf-8", "replace"),
        hashlib.sha256,
    ).hexdigest()
    return f"{scope}:{digest}"


def _login_keys(username: str, client_ip: str) -> tuple[tuple[str, str], ...]:
    return tuple((scope, _login_key(scope, username, client_ip)) for scope in LOGIN_LIMITS)


def login_retry_after(db, username: str, client_ip: str, *, now: int | None = None) -> int:
    """Return remaining lock time without revealing whether an account exists."""
    timestamp = int(time.time() if now is None else now)
    result = 0
    for _scope, key in _login_keys(username, client_ip):
        row = db.execute("SELECT blocked_until FROM login_throttle WHERE key = ?", (key,)).fetchone()
        if row is not None:
            result = max(result, int(row["blocked_until"] or 0) - timestamp)
    return max(0, result)


def record_login_failure(db, username: str, client_ip: str, *, now: int | None = None) -> int:
    """Persist bounded account/network throttles and return the lock duration."""
    timestamp = int(time.time() if now is None else now)
    retry_after = 0
    for scope, key in _login_keys(username, client_ip):
        row = db.execute(
            "SELECT failures, window_started, blocked_until FROM login_throttle WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None or timestamp - int(row["window_started"]) >= LOGIN_WINDOW_SECONDS:
            failures = 1
            window_started = timestamp
        else:
            failures = int(row["failures"]) + 1
            window_started = int(row["window_started"])
        blocked_until = max(int(row["blocked_until"] or 0) if row is not None else 0, timestamp)
        if failures >= LOGIN_LIMITS[scope]:
            blocked_until = max(blocked_until, timestamp + LOGIN_BLOCK_SECONDS)
            retry_after = max(retry_after, blocked_until - timestamp)
        db.execute(
            """INSERT INTO login_throttle(key, failures, window_started, blocked_until)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,
                   window_started=excluded.window_started, blocked_until=excluded.blocked_until""",
            (key, failures, window_started, blocked_until),
        )
    db.commit()
    return retry_after


def clear_login_failures(db, username: str, client_ip: str) -> None:
    db.executemany(
        "DELETE FROM login_throttle WHERE key = ?",
        ((key,) for _scope, key in _login_keys(username, client_ip)),
    )
    db.commit()


def purge_login_throttles(db, *, now: int | None = None) -> None:
    timestamp = int(time.time() if now is None else now)
    db.execute(
        "DELETE FROM login_throttle WHERE blocked_until < ? AND window_started < ?",
        (timestamp, timestamp - LOGIN_WINDOW_SECONDS),
    )
    db.commit()
