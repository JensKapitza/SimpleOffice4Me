"""Administrator routes for server bind and background workers."""

from __future__ import annotations


from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit
from .admin import admin_required
from .settings_store import SettingsStore
from .desktop_proxy_status import _lan_proxy_status
from .translations import translate
from .lan_proxy_config import DEFAULT_LAN_PROXY_SETTINGS, validate_lan_proxy_settings
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


@bp.get("/settings/server")
@admin_required
def server_settings():
    settings = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()
    proxy_status = _lan_proxy_status(current_app.config["DOCUMENT_ROOT"])
    return render_template(
        "admin/server_settings.html",
        server=settings["server"],
        lan_proxy=settings["lan_proxy"],
        lan_proxy_status=proxy_status,
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
        current_settings = store.settings()
        current = current_settings["server"]
        current_proxy = current_settings["lan_proxy"]
        if reset_defaults:
            server = dict(DEFAULT_SERVER_SETTINGS)
            proxy = dict(DEFAULT_LAN_PROXY_SETTINGS)
        else:
            host = request.form.get("host", current["host"]).strip()
            if not host or any(character.isspace() for character in host) or len(host) > 253:
                raise ValueError("Bitte einen gültigen Server-Host ohne Leerzeichen eingeben.")
            role = request.form.get("container_role", current["container_role"]).strip()
            if role not in CONTAINER_ROLES:
                raise ValueError("Bitte eine gültige Container-Rolle wählen.")
            proxy_enabled_values = request.form.getlist("lan_proxy_enabled")
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
            proxy = validate_lan_proxy_settings({
                "enabled": (
                    "1" in proxy_enabled_values
                    if proxy_enabled_values else current_proxy["enabled"]
                ),
                "bind_host": request.form.get("lan_proxy_host", current_proxy["bind_host"]).strip(),
                "port": _server_int(
                    request.form.get("lan_proxy_port", str(current_proxy["port"])), current_proxy["port"],
                    1024, 65535, "LAN-Proxy-Port",
                ),
                "tls_cert": request.form.get("lan_proxy_tls_cert", current_proxy["tls_cert"]).strip(),
                "tls_key": request.form.get("lan_proxy_tls_key", current_proxy["tls_key"]).strip(),
            })
        web_changed = any(server[key] != current[key] for key in server)
        proxy_changed = any(proxy[key] != current_proxy[key] for key in proxy)
        store.save({"server": server, "lan_proxy": proxy}, str(g.user["username"]))
        proxy_status = _lan_proxy_status(current_app.config["DOCUMENT_ROOT"])
        should_apply_proxy = (
            proxy_changed or (proxy["enabled"] and proxy_status["state"] != "running")
            or (not proxy["enabled"] and proxy_status["state"] == "running")
        )
        if should_apply_proxy:
            proxy_status = _apply_lan_proxy(
                proxy, current_app.config["DOCUMENT_ROOT"],
                backend_port=request.environ.get("SERVER_PORT"),
            )
        audit(
            "server_settings_updated", "settings", "server.bind",
            detail={
                **server,
                "lan_proxy_enabled": proxy["enabled"],
                "lan_proxy_bind_host": proxy["bind_host"],
                "lan_proxy_port": proxy["port"],
                "lan_proxy_tls_configured": bool(proxy["tls_cert"] and proxy["tls_key"]),
                "reset_to_defaults": reset_defaults,
                "restart_required": web_changed,
                "lan_proxy_applied": should_apply_proxy,
            },
        )
        notice = translate(g.language, "admin.server.reset") if reset_defaults else translate(
            g.language, "admin.server.saved")
        if web_changed:
            notice += " " + translate(g.language, "admin.server.restart")
        if should_apply_proxy:
            if proxy_status["state"] == "running":
                notice += " " + ("LAN-Proxy läuft jetzt." if g.language == "de" else "LAN proxy is running.")
            elif proxy["enabled"]:
                notice += " " + (
                    "LAN-Proxy konnte nicht starten; Status und TLS-Dateien prüfen."
                    if g.language == "de" else
                    "LAN proxy could not start; check its status and TLS files."
                )
            else:
                notice += " " + ("LAN-Proxy gestoppt." if g.language == "de" else "LAN proxy stopped.")
        flash(notice)
    except ValueError as exc:
        current_app.logger.info("Server setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Server settings save failed: %s", type(exc).__name__)
        flash(translate(g.language, "admin.server.failed"))
    return redirect(url_for("admin_server.server_settings"))


def _apply_lan_proxy(settings, document_root, backend_port=None):
    from .lan_proxy import lan_proxy_runtime
    from simpleoffice_server_config import effective_server_settings

    backend_host = str(current_app.config.get("LAN_PROXY_BACKEND_HOST", "127.0.0.1"))
    backend_port = int(backend_port or current_app.config.get(
        "LAN_PROXY_BACKEND_PORT", effective_server_settings(document_root)["port"],
    ))
    return lan_proxy_runtime.start(
        current_app._get_current_object(), settings,
        backend_host=backend_host, backend_port=backend_port,
        document_root=document_root,
    )


@bp.post("/settings/server/lan-proxy")
@admin_required
def control_lan_proxy():
    root = current_app.config["DOCUMENT_ROOT"]
    store = SettingsStore(root)
    settings = store.settings()["lan_proxy"]
    action = request.form.get("action", "")
    try:
        if action == "start":
            settings["enabled"] = True
            store.save({"lan_proxy": settings}, str(g.user["username"]))
            status = _apply_lan_proxy(
                settings, root, backend_port=request.environ.get("SERVER_PORT"),
            )
            message = (
                "LAN-Proxy gestartet." if status["state"] == "running" else
                "LAN-Proxy konnte nicht gestartet werden: " + status["message"]
            )
        elif action == "stop":
            settings["enabled"] = False
            store.save({"lan_proxy": settings}, str(g.user["username"]))
            from .lan_proxy import lan_proxy_runtime
            status = lan_proxy_runtime.stop(state="disabled", message="LAN proxy is disabled")
            message = "LAN-Proxy gestoppt."
        else:
            raise ValueError("Unbekannte LAN-Proxy-Aktion.")
        audit(
            "lan_proxy_lifecycle", "service", "lan-proxy",
            detail={"action": action, "state": status["state"], "bind_host": settings["bind_host"], "port": settings["port"]},
        )
        flash(message)
    except (OSError, ValueError) as exc:
        current_app.logger.info("LAN proxy control rejected (%s)", type(exc).__name__)
        flash(str(exc))
    return redirect(url_for("admin_server.server_settings"))
