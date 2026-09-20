"""Administrator UI for Mini Services and DHCP/DNS configuration."""

from __future__ import annotations

import json

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .access_control import audit, is_admin
from .auth import login_required
from .mini_services import (
    DEFAULT_CONFIG,
    clear_dns_log,
    clear_leases,
    default_config_path,
    load_config,
    read_blocklist_meta,
    read_leases,
    read_status,
    refresh_blocklists,
    save_config,
    tail_dns_log,
)
from .network_system_status import ipv4_routing_status, network_interfaces
from simpleoffice_network_gateway import DEFAULT_GATEWAY_SETTINGS
from simpleoffice_network_gateway_runtime import load_gateway_settings, save_gateway_settings


bp = Blueprint("mini_services_admin", __name__, url_prefix="/admin/mini-services")


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def _json_rows(name: str) -> list[dict]:
    raw = request.form.get(name, "").strip()
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{name} muss eine JSON-Liste aus Objekten sein")
    return value


def _network_context():
    path = default_config_path()
    try:
        config = load_config(path)
    except (ValueError, OSError):
        config = DEFAULT_CONFIG
        flash("Die Konfigurationsdatei ist nicht lesbar oder ungültig. Angezeigt werden Standardwerte; Speichern ersetzt die fehlerhafte Datei.")
    try:
        gateway = load_gateway_settings(path)
    except (ValueError, OSError):
        gateway = DEFAULT_GATEWAY_SETTINGS
        flash("Gateway-Einstellungen sind nicht lesbar. Standardwerte werden angezeigt; erneut speichern, um die Datei zu reparieren.")
    return {
        "config": config,
        "status": read_status(path),
        "leases": read_leases(path),
        "dns_queries": tail_dns_log(path, 200),
        "blocklist": read_blocklist_meta(path),
        "config_path": str(path),
        "interfaces": network_interfaces(),
        "routing": ipv4_routing_status(),
        "gateway": gateway,
    }


@bp.get("")
@admin_required
def index():
    # The hub does not need leases, query logs or privileged routing probes.
    try:
        config = load_config(default_config_path())
    except (ValueError, OSError):
        config = DEFAULT_CONFIG
        flash("Mini-Service-Konfiguration ist ungültig. Netzwerkeinstellungen prüfen.")
    return render_template(
        "admin/mini_services_hub.html",
        config=config,
        status=read_status(default_config_path()),
    )


@bp.get("/network")
@admin_required
def network():
    return render_template("admin/mini_services.html", **_network_context())


@bp.post("/save")
@admin_required
def save():
    path = default_config_path()
    try:
        previous = load_config(path)
    except (ValueError, OSError):
        previous = DEFAULT_CONFIG
    try:
        candidate = {
            "version": 1,
            "dhcp": {
                **previous["dhcp"],
                "enabled": request.form.get("dhcp_enabled") == "1",
                "bind": request.form.get("dhcp_bind", "127.0.0.1"),
                "port": request.form.get("dhcp_port", "67"),
                "interface": request.form.get("dhcp_interface", ""),
                "server_ip": request.form.get("server_ip", ""),
                "network": request.form.get("network", ""),
                "pool_start": request.form.get("pool_start", ""),
                "pool_end": request.form.get("pool_end", ""),
                "routers": _csv(request.form.get("routers", "")),
                "dns_servers": _csv(request.form.get("dns_servers", "")),
                "domain": request.form.get("domain", ""),
                "domain_search": _csv(request.form.get("domain_search", "")),
                "ntp_servers": _csv(request.form.get("ntp_servers", "")),
                "lease_time": request.form.get("lease_time", "86400"),
                "renewal_time": request.form.get("renewal_time", "43200"),
                "rebinding_time": request.form.get("rebinding_time", "75600"),
                "authoritative": request.form.get("authoritative") == "1",
                "ping_check": request.form.get("ping_check") == "1",
                "decline_hold_seconds": request.form.get("decline_hold_seconds", "600"),
                "mtu": request.form.get("mtu", "1500"),
                "next_server": request.form.get("next_server", ""),
                "tftp_server": request.form.get("tftp_server", ""),
                "boot_file": request.form.get("boot_file", ""),
                "exclusions": _csv(request.form.get("exclusions", "")),
                "reservations": previous["dhcp"]["reservations"],
                "static_routes": previous["dhcp"]["static_routes"],
                "custom_options": previous["dhcp"]["custom_options"],
            },
            "dns": {
                **previous["dns"],
                "enabled": request.form.get("dns_enabled") == "1",
                "bind": _csv(request.form.get("dns_bind", "127.0.0.1")),
                "port": request.form.get("dns_port", "53"),
                "upstreams": _csv(request.form.get("upstreams", "")),
                "timeout": request.form.get("dns_timeout", "2.0"),
                "cache_enabled": request.form.get("cache_enabled") == "1",
                "cache_max_entries": request.form.get("cache_max_entries", "10000"),
                "query_log": request.form.get("query_log") == "1",
                "query_log_max_bytes": request.form.get("query_log_max_bytes", str(10 * 1024 * 1024)),
                "block_mode": request.form.get("block_mode", "zero"),
                "records": previous["dns"]["records"],
                "manual_blocks": _csv(request.form.get("manual_blocks", "")),
                "allowlist": _csv(request.form.get("allowlist", "")),
                "blocklist_urls": [line.strip() for line in request.form.get("blocklist_urls", "").splitlines() if line.strip()],
                "blocklist_refresh_hours": request.form.get("blocklist_refresh_hours", "24"),
            },
        }
        candidate["dhcp"]["reservations"] = _json_rows("reservations")
        candidate["dhcp"]["static_routes"] = _json_rows("static_routes")
        candidate["dhcp"]["custom_options"] = json.loads(request.form.get("custom_options", "{}") or "{}")
        candidate["dns"]["records"] = _json_rows("dns_records")
        if not isinstance(candidate["dhcp"]["custom_options"], dict):
            raise ValueError("custom_options muss ein JSON-Objekt sein")
        clean = save_config(candidate, path)
    except (ValueError, TypeError, OSError) as exc:
        storage_error = isinstance(exc, OSError)
        message = (
            "Die Konfiguration konnte nicht geschrieben werden. Dateirechte und freien Speicher prüfen und erneut versuchen."
            if storage_error else
            "Die Konfiguration ist ungültig. IP-Adressen, Netz und Pool, Ports, Zeitwerte sowie JSON-Felder prüfen."
        )
        flash(message + " Deine Eingaben bleiben erhalten; die gespeicherte Konfiguration wurde nicht geändert.")
        audit("mini_services_config_rejected", "service", "dhcp-dns", detail={"error_type": type(exc).__name__})
        return render_template(
            "admin/mini_services.html", **{**_network_context(), "config": candidate},
            submitted_json=request.form, configuration_error=True,
        ), 503 if storage_error else 400
    audit(
        "mini_services_config_updated",
        "service",
        "dhcp-dns",
        detail={"dhcp_enabled": clean["dhcp"]["enabled"], "dns_enabled": clean["dns"]["enabled"]},
    )
    flash("Mini Services gespeichert. Der Netzwerk-Worker übernimmt die Änderung automatisch.")
    return redirect(url_for("mini_services_admin.network"))


@bp.post("/gateway/settings")
@admin_required
def gateway_settings():
    candidate = {key: request.form.get(key, str(default)) for key, default in DEFAULT_GATEWAY_SETTINGS.items()}
    for key, default in DEFAULT_GATEWAY_SETTINGS.items():
        if isinstance(default, bool):
            candidate[key] = request.form.get(key) == "1"
    try:
        if request.form.get("action") == "reset":
            candidate = DEFAULT_GATEWAY_SETTINGS
        clean = save_gateway_settings(candidate, default_config_path())
    except (OSError, ValueError, TypeError):
        flash("Gateway-Einstellungen nicht gespeichert. Modus, Schnittstellen, IPv4-Netz und Dateirechte prüfen.")
        return render_template("admin/mini_services.html", **{**_network_context(), "gateway": candidate}), 400
    audit("mini_gateway_settings_updated", "service", "gateway", detail={"enabled": clean["enabled"], "mode": clean["mode"]})
    flash("Gateway-Einstellungen gespeichert. Der Worker übernimmt die Änderung; Status in der Dienstübersicht prüfen.")
    return redirect(url_for("mini_services_admin.network"))


@bp.post("/network/<service>/reset")
@admin_required
def reset_network(service):
    if service not in {"dhcp", "dns"}:
        abort(404)
    try:
        config = load_config(default_config_path())
        config[service] = DEFAULT_CONFIG[service]
        save_config(config, default_config_path())
    except (OSError, ValueError):
        flash("Standardwerte konnten nicht gespeichert werden. Gesamtkonfiguration und Dateirechte prüfen.")
        return redirect(url_for("mini_services_admin.network"))
    audit("mini_service_defaults_restored", "service", service)
    flash(f"{service.upper()}: deaktivierte Standardwerte wiederhergestellt. Andere Dienste bleiben unverändert.")
    return redirect(url_for("mini_services_admin.network"))


@bp.post("/blocklists/refresh")
@admin_required
def refresh_lists():
    path = default_config_path()
    try:
        meta = refresh_blocklists(load_config(path), path)
    except Exception as exc:
        audit("mini_dns_blocklist_refresh", "service", "dns", outcome="failure", detail={"error_type": type(exc).__name__})
        flash("Blocklisten konnten nicht aktualisiert werden. Netzwerk und Blocklisten-Adressen prüfen; Details im Audit.")
    else:
        audit("mini_dns_blocklist_refresh", "service", "dns", detail={"domains": meta.get("domains", 0)})
        flash(f"Blocklisten aktualisiert: {meta.get('domains', 0)} Domains.")
    return redirect(url_for("mini_services_admin.network"))


@bp.post("/dns-log/clear")
@admin_required
def clear_query_log():
    clear_dns_log(default_config_path())
    audit("mini_dns_log_cleared", "service", "dns")
    flash("DNS-Anfragelog wurde geleert.")
    return redirect(url_for("mini_services_admin.network"))


@bp.post("/leases/clear")
@admin_required
def clear_dhcp_leases():
    clear_leases(default_config_path())
    audit("mini_dhcp_leases_cleared", "service", "dhcp")
    flash("DHCP-Leases wurden geleert. Aktive Clients können anschließend neue Leases anfordern.")
    return redirect(url_for("mini_services_admin.network"))


# This module is imported while the main Flask app is already being built.
# Register focused Mini Service extensions here so the large central application
# module stays untouched.
from . import app as _flask_app
from .audio_output_admin import bp as _audio_output_admin_bp
from .audio_streamer_admin import bp as _audio_streamer_admin_bp
from .network_boot_admin import bp as _network_boot_admin_bp
from .media_renderer_admin import bp as _media_renderer_admin_bp
from .network_boot_http import bp as _network_boot_http_bp, federation_bp as _network_boot_federation_bp
from .telephony_admin import bp as _telephony_admin_bp
from .mini_services_api import bp as _mini_services_api_bp
for _service_bp in (
    _network_boot_admin_bp,
    _network_boot_http_bp,
    _network_boot_federation_bp,
    _audio_output_admin_bp,
    _audio_streamer_admin_bp,
    _media_renderer_admin_bp,
    _telephony_admin_bp,
    _mini_services_api_bp,
):
    if _service_bp.name not in _flask_app.blueprints:
        _flask_app.register_blueprint(_service_bp)
