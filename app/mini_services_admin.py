"""Administrator UI for Mini Services and DHCP/DNS configuration."""

from __future__ import annotations

import json

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from simpleoffice_dhcp_profiles import (
    clear_profile_leases,
    load_profiles,
    read_profile_leases,
    save_profiles,
)
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
from .network_system_status import ipv4_routing_status, network_interfaces


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


def _submitted_dhcp_profiles(previous: list[dict]) -> list[dict]:
    ids = request.form.getlist("dhcp_profile_id")
    columns = {
        name: request.form.getlist(name)
        for name in (
            "dhcp_profile_enabled", "dhcp_profile_interface", "dhcp_profile_bind",
            "dhcp_profile_port", "dhcp_profile_server_ip", "dhcp_profile_network",
            "dhcp_profile_pool_start", "dhcp_profile_pool_end",
            "dhcp_profile_routers", "dhcp_profile_dns_servers",
        )
    }
    previous_by_id = {str(row.get("id")): row for row in previous}

    def value(name: str, index: int, fallback=""):
        values = columns[name]
        return values[index] if index < len(values) else fallback

    rows = []
    for index, raw_id in enumerate(ids):
        profile_id = raw_id.strip()
        if not profile_id:
            continue
        old = previous_by_id.get(profile_id, {})
        rows.append({
            **old,
            "id": profile_id,
            "enabled": value("dhcp_profile_enabled", index, "0") == "1",
            "interface": value("dhcp_profile_interface", index, old.get("interface", "")),
            "bind": value("dhcp_profile_bind", index, old.get("bind", "127.0.0.1")),
            "port": value("dhcp_profile_port", index, old.get("port", 67)),
            "server_ip": value("dhcp_profile_server_ip", index, old.get("server_ip", "")),
            "network": value("dhcp_profile_network", index, old.get("network", "")),
            "pool_start": value("dhcp_profile_pool_start", index, old.get("pool_start", "")),
            "pool_end": value("dhcp_profile_pool_end", index, old.get("pool_end", "")),
            "routers": _csv(value("dhcp_profile_routers", index, ",".join(old.get("routers", [])))),
            "dns_servers": _csv(value("dhcp_profile_dns_servers", index, ",".join(old.get("dns_servers", [])))),
        })
    return rows


def _network_context():
    path = default_config_path()
    config = load_config(path)
    profiles = load_profiles(path, config["dhcp"])
    leases = [{**row, "dhcp_profile": "default"} for row in read_leases(path)]
    leases.extend(read_profile_leases(path, profiles))
    return {
        "config": config,
        "dhcp_profiles": profiles,
        "status": read_status(path),
        "leases": leases,
        "dns_queries": tail_dns_log(path, 200),
        "blocklist": read_blocklist_meta(path),
        "config_path": str(path),
        "interfaces": network_interfaces(),
        "routing": ipv4_routing_status(),
    }


@bp.get("")
@admin_required
def index():
    context = _network_context()
    return render_template(
        "admin/mini_services_hub.html",
        config=context["config"],
        status=context["status"],
        dhcp_profiles=context["dhcp_profiles"],
    )


@bp.get("/network")
@admin_required
def network():
    return render_template("admin/mini_services.html", **_network_context())


@bp.get("/dhcp-profiles")
@admin_required
def dhcp_profiles():
    context = _network_context()
    return render_template("admin/dhcp_profiles.html", **context)


@bp.post("/dhcp-profiles/save")
@admin_required
def save_dhcp_profiles():
    path = default_config_path()
    config = load_config(path)
    previous = load_profiles(path, config["dhcp"])
    try:
        rows = _submitted_dhcp_profiles(previous)
        profiles = save_profiles(rows, path, config["dhcp"])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        flash(f"DHCP-Netze wurden nicht gespeichert: {exc}")
        return redirect(url_for("mini_services_admin.dhcp_profiles"))
    audit(
        "mini_dhcp_profiles_updated",
        "service",
        "dhcp",
        detail={"profiles": len(profiles), "enabled": sum(1 for row in profiles if row.get("enabled"))},
    )
    flash("Zusätzliche DHCP-Netze gespeichert. Der Worker übernimmt die Änderung automatisch.")
    return redirect(url_for("mini_services_admin.dhcp_profiles"))


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
        # Existing additional profiles must remain valid when the primary DHCP
        # configuration changes. Loading performs the full cross-check.
        load_profiles(path, clean["dhcp"])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        flash(f"Mini Services wurden nicht gespeichert: {exc}")
        return redirect(url_for("mini_services_admin.network"))
    audit(
        "mini_services_config_updated",
        "service",
        "dhcp-dns",
        detail={"dhcp_enabled": clean["dhcp"]["enabled"], "dns_enabled": clean["dns"]["enabled"]},
    )
    flash("Mini Services gespeichert. Der Netzwerk-Worker übernimmt die Änderung automatisch.")
    return redirect(url_for("mini_services_admin.network"))


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
    path = default_config_path()
    config = load_config(path)
    profiles = load_profiles(path, config["dhcp"])
    clear_leases(path)
    clear_profile_leases(path, profiles)
    audit("mini_dhcp_leases_cleared", "service", "dhcp")
    flash("DHCP-Leases aller DHCP-Profile wurden geleert. Aktive Clients können anschließend neue Leases anfordern.")
    return redirect(url_for("mini_services_admin.network"))


# This module is imported while the main Flask app is already being built.
# Register focused Mini Service extensions here so the large central application
# module stays untouched.
from . import app as _flask_app
from .audio_output_admin import bp as _audio_output_admin_bp
from .audio_streamer_admin import bp as _audio_streamer_admin_bp
from .network_boot_admin import bp as _network_boot_admin_bp
from .network_boot_http import bp as _network_boot_http_bp, federation_bp as _network_boot_federation_bp
from .telephony_admin import bp as _telephony_admin_bp
for _service_bp in (
    _network_boot_admin_bp,
    _network_boot_http_bp,
    _network_boot_federation_bp,
    _audio_output_admin_bp,
    _audio_streamer_admin_bp,
    _telephony_admin_bp,
):
    if _service_bp.name not in _flask_app.blueprints:
        _flask_app.register_blueprint(_service_bp)
