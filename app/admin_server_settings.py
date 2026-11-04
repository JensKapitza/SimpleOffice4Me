"""Administrator routes for server bind and background workers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit
from .admin import admin_required
from .settings_store import SettingsStore
from .translations import translate
from simpleoffice_server_config import (
    CONTAINER_ROLES,
    DEFAULT_SERVER_SETTINGS,
    MAX_SERVER_PORT,
    MAX_TRUSTED_PROXY_HOPS,
    MAX_WSGI_CHANNEL_TIMEOUT,
    MAX_WSGI_THREADS,
    MIN_SERVER_PORT,
    MIN_TRUSTED_PROXY_HOPS,
    MIN_WSGI_CHANNEL_TIMEOUT,
    MIN_WSGI_THREADS,
)

bp = Blueprint("admin_server", __name__, url_prefix="/admin")


def _desktop_proxy_status(document_root):
    """Read the desktop proxy heartbeat without trusting stale process state."""
    result = {"state": "unknown", "message": "No desktop proxy status available", "origin": ""}
    path = Path(document_root) / ".simpleoffice-meta" / "desktop-proxy-status.json"
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict) or content.get("schema") != 1:
            return result
        updated = datetime.fromisoformat(str(content["updated_at"]).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > 20:
            return {"state": "stale", "message": "Desktop proxy status is outdated", "origin": ""}
        state = str(content.get("state", "unknown"))
        if state not in {"running", "disabled", "stopped", "error"}:
            return result
        origin = str(content.get("origin", ""))
        if len(origin) > 255 or not origin.startswith("http://"):
            origin = ""
        return {
            "state": state,
            "message": str(content.get("message", ""))[:240],
            "origin": origin if state == "running" else "",
        }
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return result


@bp.get("/settings/server")
@admin_required
def server_settings():
    settings = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()
    proxy_status = _desktop_proxy_status(current_app.config["DOCUMENT_ROOT"])
    return render_template(
        "admin/server_settings.html",
        server=settings["server"],
        desktop_proxy_status=proxy_status,
        port_min=MIN_SERVER_PORT,
        port_max=MAX_SERVER_PORT,
        threads_min=MIN_WSGI_THREADS,
        threads_max=MAX_WSGI_THREADS,
        timeout_min=MIN_WSGI_CHANNEL_TIMEOUT,
        timeout_max=MAX_WSGI_CHANNEL_TIMEOUT,
        hops_min=MIN_TRUSTED_PROXY_HOPS,
        hops_max=MAX_TRUSTED_PROXY_HOPS,
    )


def _server_int(form_value, current, minimum, maximum, label):
    if form_value is None:
        return current
    if not form_value.isascii() or not form_value.isdigit() or len(form_value) > len(str(maximum)):
        raise ValueError(f"Bitte einen gültigen Wert für {label} eingeben.")
    parsed = int(form_value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{label} muss zwischen {minimum} und {maximum} liegen.")
    return parsed


@bp.post("/settings/server")
@admin_required
def save_server_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["server"]
        if reset_defaults:
            server = dict(DEFAULT_SERVER_SETTINGS)
        else:
            host = request.form.get("host", current["host"]).strip()
            if not host or any(character.isspace() for character in host) or len(host) > 253:
                raise ValueError("Bitte einen gültigen Server-Host ohne Leerzeichen eingeben.")
            role = request.form.get("container_role", current["container_role"]).strip()
            if role not in CONTAINER_ROLES:
                raise ValueError("Bitte eine gültige Container-Rolle wählen.")
            server = {
                "host": host,
                "port": _server_int(
                    request.form.get("port"), current["port"],
                    MIN_SERVER_PORT, MAX_SERVER_PORT, "Server-Port",
                ),
                "wsgi_threads": _server_int(
                    request.form.get("wsgi_threads"), current["wsgi_threads"],
                    MIN_WSGI_THREADS, MAX_WSGI_THREADS, "WSGI-Threads",
                ),
                "wsgi_channel_timeout": _server_int(
                    request.form.get("wsgi_channel_timeout"), current["wsgi_channel_timeout"],
                    MIN_WSGI_CHANNEL_TIMEOUT, MAX_WSGI_CHANNEL_TIMEOUT, "WSGI-Kanalzeitlimit",
                ),
                "trusted_proxy_hops": _server_int(
                    request.form.get("trusted_proxy_hops"), current["trusted_proxy_hops"],
                    MIN_TRUSTED_PROXY_HOPS, MAX_TRUSTED_PROXY_HOPS, "Proxy-Hops",
                ),
                "background_index_enabled": request.form.get("background_index_enabled", "") == "1",
                "osm_index_enabled": request.form.get("osm_index_enabled", "") == "1",
                "datalogger_enabled": request.form.get("datalogger_enabled", "") == "1",
                "mini_services_autostart": request.form.get("mini_services_autostart", "") == "1",
                "container_role": role,
            }
        changed = any(server[key] != current[key] for key in server)
        store.save({"server": server}, str(g.user["username"]))
        audit(
            "server_settings_updated", "settings", "server.bind",
            detail={**server, "reset_to_defaults": reset_defaults, "restart_required": changed},
        )
        notice = translate(g.language, "admin.server.reset") if reset_defaults else translate(
            g.language, "admin.server.saved")
        if changed:
            notice += " " + translate(g.language, "admin.server.restart")
        flash(notice)
    except ValueError as exc:
        current_app.logger.info("Server setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Server settings save failed: %s", type(exc).__name__)
        flash(translate(g.language, "admin.server.failed"))
    return redirect(url_for("admin_server.server_settings"))
