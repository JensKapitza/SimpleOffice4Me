"""Admin UI and authenticated ICE credentials for Connectivity Relay."""
from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import audit
from .auth import login_required
from .mini_services import default_config_path, read_status
from .mini_services_admin import admin_required
from simpleoffice_connection_relay import (
    DEFAULT_RELAY_SETTINGS,
    ensure_turn_secret,
    https_proxy_profile,
    ice_servers,
    load_relay_settings,
    proxy_password,
    save_relay_secrets,
    save_relay_settings,
    ssh_proxy_command,
)


bp = Blueprint("connectivity_relay_admin", __name__, url_prefix="/admin/mini-services/connectivity")
ice_bp = Blueprint("connectivity_relay_ice", __name__, url_prefix="/api/connectivity")


def _targets(raw: str) -> list[str]:
    return [line.strip() for line in str(raw or "").replace(",", "\n").splitlines() if line.strip()]


def _context(settings=None):
    path = default_config_path()
    try:
        current = settings or load_relay_settings(path)
    except (OSError, ValueError):
        current = dict(DEFAULT_RELAY_SETTINGS)
        flash("Connectivity-Relay-Konfiguration ist nicht lesbar. Angezeigt werden sichere Standardwerte.", "error")
    status = read_status(path)
    relay_state = status.get("services", {}).get("relay", {})
    commands = []
    for target in current.get("tunnel_targets", []):
        try:
            commands.append({"target": target, "command": ssh_proxy_command(path, target)})
        except ValueError:
            pass
    return {
        "settings": current,
        "relay_state": relay_state,
        "turnserver_available": bool(shutil.which("turnserver")),
        "turnserver_path": shutil.which("turnserver") or "",
        "proxy": https_proxy_profile(path) if current.get("https_proxy_enabled") else {
            "enabled": False,
            "proxy_url": current.get("https_proxy_url", ""),
            "username": current.get("https_proxy_username", ""),
            "targets": current.get("tunnel_targets", []),
            "password_configured": bool(proxy_password(path)),
        },
        "ssh_commands": commands,
        "config_path": str(path),
    }


@bp.get("")
@admin_required
def index():
    return render_template("admin/connectivity_relay.html", **_context())


@bp.post("/save")
@admin_required
def save():
    path = default_config_path()
    candidate = {
        "enabled": request.form.get("enabled") == "1",
        "public_host": request.form.get("public_host", ""),
        "listen_ip": request.form.get("listen_ip", "127.0.0.1"),
        "relay_ip": request.form.get("relay_ip", "127.0.0.1"),
        "external_ip": request.form.get("external_ip", ""),
        "realm": request.form.get("realm", "simpleoffice.local"),
        "turn_port": request.form.get("turn_port", "3478"),
        "tls_enabled": request.form.get("tls_enabled") == "1",
        "turn_tls_port": request.form.get("turn_tls_port", "5349"),
        "min_port": request.form.get("min_port", "49160"),
        "max_port": request.form.get("max_port", "49200"),
        "credential_ttl": request.form.get("credential_ttl", "3600"),
        "tls_cert": request.form.get("tls_cert", ""),
        "tls_key": request.form.get("tls_key", ""),
        "https_proxy_enabled": request.form.get("https_proxy_enabled") == "1",
        "https_proxy_url": request.form.get("https_proxy_url", ""),
        "https_proxy_username": request.form.get("https_proxy_username", ""),
        "https_proxy_ca_file": request.form.get("https_proxy_ca_file", ""),
        "tunnel_targets": _targets(request.form.get("tunnel_targets", "")),
    }
    try:
        clean = save_relay_settings(candidate, path)
        password = request.form.get("https_proxy_password", "")
        if password:
            save_relay_secrets(path, proxy_password=password)
        if clean["enabled"]:
            ensure_turn_secret(path)
    except (OSError, TypeError, ValueError):
        flash("Relay-Einstellungen sind ungültig oder konnten nicht gespeichert werden. Host, IPs, Ports, TLS-Dateien und Tunnel-Ziele prüfen.", "error")
        return render_template("admin/connectivity_relay.html", **_context(candidate)), 400
    audit(
        "mini_connectivity_relay_updated",
        "service",
        "relay",
        detail={
            "enabled": clean["enabled"],
            "tls_enabled": clean["tls_enabled"],
            "https_proxy_enabled": clean["https_proxy_enabled"],
            "tunnel_targets": len(clean["tunnel_targets"]),
        },
    )
    flash("Connectivity Relay gespeichert. Der Mini-Services-Worker übernimmt die Änderung automatisch.")
    return redirect(url_for("connectivity_relay_admin.index"))


@bp.post("/rotate-turn-secret")
@admin_required
def rotate_turn_secret():
    path = default_config_path()
    try:
        save_relay_secrets(path, turn_secret=secrets.token_urlsafe(32))
    except (OSError, ValueError):
        flash("TURN-Shared-Secret konnte nicht erneuert werden.", "error")
    else:
        audit("mini_turn_secret_rotated", "service", "relay")
        flash("TURN-Shared-Secret erneuert. Bestehende kurzlebige TURN-Zugänge laufen dadurch aus.")
    return redirect(url_for("connectivity_relay_admin.index"))


@ice_bp.get("/ice")
@login_required
def ice():
    path = default_config_path()
    try:
        settings = load_relay_settings(path)
        status = read_status(path)
        relay_state = status.get("services", {}).get("relay", {}).get("state", "unavailable")
        if not settings["enabled"] or relay_state not in {"running", "degraded"}:
            return jsonify({"iceServers": [], "relay": relay_state, "mode": "direct"})
        principal = str(g.user["username"])
        servers = ice_servers(path, principal)
        return jsonify({"iceServers": servers, "relay": relay_state, "mode": "stun-turn"})
    except (OSError, ValueError):
        return jsonify({"iceServers": [], "relay": "unavailable", "mode": "direct"})


@ice_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
