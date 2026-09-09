"""Administrator UI for DHCP/DNS Mini Services."""

from __future__ import annotations

import json

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .access_control import audit, is_admin
from .auth import login_required
from .mini_services import (
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


@bp.get("")
@admin_required
def index():
    path = default_config_path()
    return render_template(
        "admin/mini_services.html",
        config=load_config(path),
        status=read_status(path),
        leases=read_leases(path),
        dns_queries=tail_dns_log(path, 200),
        blocklist=read_blocklist_meta(path),
        config_path=str(path),
    )


@bp.post("/save")
@admin_required
def save():
    path = default_config_path()
    previous = load_config(path)
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
                "reservations": _json_rows("reservations"),
                "static_routes": _json_rows("static_routes"),
                "custom_options": json.loads(request.form.get("custom_options", "{}") or "{}"),
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
                "records": _json_rows("dns_records"),
                "manual_blocks": _csv(request.form.get("manual_blocks", "")),
                "allowlist": _csv(request.form.get("allowlist", "")),
                "blocklist_urls": [line.strip() for line in request.form.get("blocklist_urls", "").splitlines() if line.strip()],
                "blocklist_refresh_hours": request.form.get("blocklist_refresh_hours", "24"),
            },
        }
        if not isinstance(candidate["dhcp"]["custom_options"], dict):
            raise ValueError("custom_options muss ein JSON-Objekt sein")
        clean = save_config(candidate, path)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        flash(f"Mini Services wurden nicht gespeichert: {exc}")
        return redirect(url_for("mini_services_admin.index"))
    audit(
        "mini_services_config_updated",
        "service",
        "dhcp-dns",
        detail={"dhcp_enabled": clean["dhcp"]["enabled"], "dns_enabled": clean["dns"]["enabled"]},
    )
    flash("Mini Services gespeichert. Der Netzwerk-Worker übernimmt die Änderung automatisch.")
    return redirect(url_for("mini_services_admin.index"))


@bp.post("/blocklists/refresh")
@admin_required
def refresh_lists():
    path = default_config_path()
    try:
        meta = refresh_blocklists(load_config(path), path)
    except Exception as exc:
        audit("mini_dns_blocklist_refresh", "service", "dns", outcome="failure", detail={"error_type": type(exc).__name__})
        flash(f"Blocklisten konnten nicht aktualisiert werden: {exc}")
    else:
        audit("mini_dns_blocklist_refresh", "service", "dns", detail={"domains": meta.get("domains", 0)})
        flash(f"Blocklisten aktualisiert: {meta.get('domains', 0)} Domains.")
    return redirect(url_for("mini_services_admin.index"))


@bp.post("/dns-log/clear")
@admin_required
def clear_query_log():
    clear_dns_log(default_config_path())
    audit("mini_dns_log_cleared", "service", "dns")
    flash("DNS-Anfragelog wurde geleert.")
    return redirect(url_for("mini_services_admin.index"))


@bp.post("/leases/clear")
@admin_required
def clear_dhcp_leases():
    clear_leases(default_config_path())
    audit("mini_dhcp_leases_cleared", "service", "dhcp")
    flash("DHCP-Leases wurden geleert. Aktive Clients können anschließend neue Leases anfordern.")
    return redirect(url_for("mini_services_admin.index"))


# This module is imported while the main Flask app is already being built.
# Register the Networkboot UI and its HTTP/federation delivery routes here so
# the large central application module stays untouched.
from . import app as _flask_app
from .network_boot_admin import bp as _network_boot_admin_bp
from .network_boot_http import bp as _network_boot_http_bp, federation_bp as _network_boot_federation_bp
for _network_bp in (_network_boot_admin_bp, _network_boot_http_bp, _network_boot_federation_bp):
    if _network_bp.name not in _flask_app.blueprints:
        _flask_app.register_blueprint(_network_bp)
