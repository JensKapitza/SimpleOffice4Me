"""Authenticated, bounded same-origin webpage export using the existing Playwright stack."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from flask import Blueprint, abort, current_app, g, request, send_file, session

from .access_control import audit, is_admin
from .db import get_db

bp = Blueprint("web_export", __name__, url_prefix="/web-export")
_TOKEN_PREFIX = "so_export_"
_token_cache: dict[int, tuple[str, int]] = {}
_token_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _settings() -> tuple[int, int]:
    row = get_db().execute(
        "SELECT token_minutes, export_uses FROM web_export_setting WHERE id=1"
    ).fetchone()
    if row is None:
        return 5, 5
    return max(1, int(row["token_minutes"])), max(1, int(row["export_uses"]))


def _loopback_request_base_url() -> str:
    """Return the current request origin only when it is unambiguously loopback."""
    value = request.url_root.strip().rstrip("/")
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if hostname == "localhost":
        return value
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return value
    except ValueError:
        pass
    return ""


def _configured_base_url() -> str:
    value = str(
        current_app.config.get("WEB_EXPORT_BASE_URL")
        or os.environ.get("SIMPLEOFFICE_SERVER_PUBLIC_URL", "")
        or _loopback_request_base_url()
    ).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError(
            "Für Webseitenexport ist SIMPLEOFFICE_SERVER_PUBLIC_URL erforderlich "
            "(außer bei lokalem Zugriff über localhost/Loopback)."
        )
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise RuntimeError("SIMPLEOFFICE_SERVER_PUBLIC_URL muss nur Schema, Host und optional Port enthalten.")
    return value


def _safe_target(raw: str) -> str:
    value = str(raw or "/").strip()
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        abort(400, description="Es können ausschließlich interne Anwendungsseiten exportiert werden.")
    if parsed.path.startswith("/web-export/"):
        abort(400, description="Export-Endpunkte können nicht exportiert werden.")
    return value[:4000]


def _new_token(user) -> str:
    minutes, uses = _settings()
    secret = _TOKEN_PREFIX + secrets.token_urlsafe(40)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    now = _now()
    expires = now + timedelta(minutes=minutes)
    db = get_db()
    cur = db.execute(
        """INSERT INTO web_export_token(
               user_id, token_hash, token_prefix, auth_version, created_at, expires_at,
               remaining_uses, revoked_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
        (user["id"], digest, secret[:16], user["auth_version"], now.isoformat(), expires.isoformat(), uses),
    )
    db.commit()
    _token_cache[int(user["id"])] = (secret, int(cur.lastrowid))
    return secret


def _renderer_token(user) -> str:
    with _token_lock:
        cached = _token_cache.get(int(user["id"]))
        if cached:
            secret, token_id = cached
            row = get_db().execute(
                """SELECT remaining_uses, expires_at, revoked_at, auth_version
                   FROM web_export_token WHERE id=? AND user_id=?""",
                (token_id, user["id"]),
            ).fetchone()
            if row is not None and not row["revoked_at"] and int(row["remaining_uses"]) > 0:
                try:
                    valid_time = datetime.fromisoformat(row["expires_at"]) > _now()
                except (TypeError, ValueError):
                    valid_time = False
                if valid_time and int(row["auth_version"]) == int(user["auth_version"]):
                    return secret
            _token_cache.pop(int(user["id"]), None)
        return _new_token(user)


def _consume_token(secret: str):
    if not isinstance(secret, str) or not secret.startswith(_TOKEN_PREFIX) or len(secret) > 256:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    db = get_db()
    now = _now()
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            """SELECT t.id AS token_id,t.user_id,t.user_id AS id,t.auth_version,t.expires_at,t.revoked_at,
                      t.remaining_uses,u.username,u.is_admin,u.is_disabled,u.auth_version AS current_auth_version
               FROM web_export_token t JOIN user u ON u.id=t.user_id
               WHERE t.token_hash=?""",
            (digest,),
        ).fetchone()
        valid = False
        if row is not None and not row["revoked_at"] and not row["is_disabled"]:
            try:
                valid = (
                    datetime.fromisoformat(row["expires_at"]) > now
                    and int(row["remaining_uses"]) > 0
                    and int(row["auth_version"]) == int(row["current_auth_version"])
                )
            except (TypeError, ValueError):
                valid = False
        if not valid:
            db.rollback()
            return None
        changed = db.execute(
            """UPDATE web_export_token
               SET remaining_uses=remaining_uses-1,last_used_at=?
               WHERE id=? AND revoked_at IS NULL AND remaining_uses>0""",
            (now.isoformat(), row["token_id"]),
        )
        if changed.rowcount != 1:
            db.rollback()
            return None
        db.commit()
        return row
    except Exception:
        db.rollback()
        raise


@bp.get("/session")
def renderer_session():
    secret = request.headers.get("X-SimpleOffice-Export-Token", "")
    row = _consume_token(secret)
    if row is None:
        abort(401)
    session.clear()
    session["user_id"] = int(row["user_id"])
    session["auth_version"] = int(row["auth_version"])
    session["web_export_read_only"] = True
    session.permanent = False
    audit("web_export_login", "session", target_id=str(row["token_id"]), outcome="success", actor=row)
    return ("", 204, {"Cache-Control": "no-store"})


@bp.before_app_request
def enforce_renderer_read_only():
    if session.get("web_export_read_only") and request.method not in {"GET", "HEAD", "OPTIONS"}:
        abort(403, description="Die Export-Sitzung ist ausschließlich lesend.")


@bp.post("/download")
def download():
    if g.get("user") is None:
        abort(401)
    target = _safe_target(request.form.get("target", "/"))
    export_format = request.form.get("format", "pdf").strip().lower()
    media = request.form.get("media", "print").strip().lower()
    if export_format not in {"pdf", "png"} or media not in {"print", "screen"}:
        abort(400, description="Ungültiges Exportformat.")
    try:
        base_url = _configured_base_url()
    except RuntimeError as exc:
        abort(503, description=str(exc))

    # Keep path components independent from request data. The suffix is selected only
    # from fixed literals after the format allowlist above.
    suffix = ".pdf" if export_format == "pdf" else ".png"
    output = tempfile.NamedTemporaryFile(prefix="simpleoffice-export-", suffix=suffix, delete=False)
    output.close()
    output_path = Path(output.name)
    token = _renderer_token(g.user)
    payload = {
        "base_url": base_url,
        "target": target,
        "format": export_format,
        "media": media,
        "output": str(output_path),
        "token": token,
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.web_export_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=60,
            cwd=str(Path(current_app.root_path).parent),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        if proc.returncode == 23:
            with _token_lock:
                _token_cache.pop(int(g.user["id"]), None)
            token = _renderer_token(g.user)
            payload["token"] = token
            proc = subprocess.run(
                [sys.executable, "-m", "app.web_export_worker"],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=60,
                cwd=str(Path(current_app.root_path).parent),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        if proc.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
            current_app.logger.warning(
                "web_export_failed request_id=%s code=%s error=%s",
                getattr(g, "request_id", ""), proc.returncode, (proc.stderr or "")[-500:].replace(token, "[redacted]"),
            )
            abort(503, description="Webseitenexport konnte nicht gerendert werden. Playwright/Chromium prüfen.")
        audit("web_export", "page", target_id=target, outcome="success", detail={"format": export_format, "media": media})
        filename = "simpleoffice-seite" + suffix
        response = send_file(output_path, as_attachment=True, download_name=filename)
        response.call_on_close(lambda: output_path.unlink(missing_ok=True))
        return response
    except subprocess.TimeoutExpired:
        output_path.unlink(missing_ok=True)
        audit("web_export", "page", target_id=target, outcome="timeout", detail={"format": export_format, "media": media})
        abort(504, description="Webseitenexport hat das Zeitlimit überschritten.")


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if g.get("user") is None or not is_admin(g.user):
        abort(403)
    db = get_db()
    if request.method == "POST":
        try:
            minutes = max(1, min(int(request.form.get("token_minutes", "5")), 1440))
            uses = max(1, min(int(request.form.get("export_uses", "5")), 100))
        except ValueError:
            abort(400, description="Export-Grenzen müssen ganze Zahlen sein.")
        db.execute(
            """INSERT INTO web_export_setting(id,token_minutes,export_uses,updated_at,updated_by)
               VALUES(1,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET token_minutes=excluded.token_minutes,
                   export_uses=excluded.export_uses,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (minutes, uses, _now().isoformat(), g.user["id"]),
        )
        db.commit()
        audit("web_export_settings", "settings", target_id="1", outcome="success", detail={"minutes": minutes, "uses": uses})
    minutes, uses = _settings()
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Webseitenexport</title></head>
<body><h1>Webseitenexport</h1><form method="post">
<input type="hidden" name="_csrf_token" value="{__import__('html').escape(str(session.get('_csrf_token','')))}">
<label>Token-Gültigkeit (Minuten) <input name="token_minutes" type="number" min="1" max="1440" value="{minutes}"></label><br>
<label>Exporte je Token <input name="export_uses" type="number" min="1" max="100" value="{uses}"></label><br>
<button type="submit">Speichern</button></form></body></html>"""


@bp.post("/revoke")
def revoke():
    if g.get("user") is None:
        abort(401)
    db = get_db()
    db.execute(
        "UPDATE web_export_token SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
        (_now().isoformat(), g.user["id"]),
    )
    db.commit()
    with _token_lock:
        _token_cache.pop(int(g.user["id"]), None)
    audit("web_export_revoke", "token", target_id=str(g.user["id"]), outcome="success")
    return ("", 204)
