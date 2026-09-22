"""Administrator UI/API for safe Linux host-firewall management."""
from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from flask import Blueprint, abort, jsonify, render_template, request

from .access_control import audit
from .mini_services import default_config_path
from .mini_services_admin import admin_required
from simpleoffice_firewall import (
    FirewallControlStore,
    firewall_status,
    normalize_rules,
    rules_for_service,
    service_port_inventory,
    validate_test_id,
)

bp = Blueprint("firewall_admin", __name__, url_prefix="/admin/mini-services/firewall")
api_bp = Blueprint("firewall_api", __name__, url_prefix="/api/mini-services/firewall")


def _store() -> FirewallControlStore:
    return FirewallControlStore(default_config_path())


def _current_web_port() -> int:
    parsed = urlsplit(request.url_root)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _backend_zone(data: dict) -> str:
    zone = str(data.get("zone") or "").strip()
    snapshot = _store().snapshot()
    zones = snapshot.get("active_zones", []) if isinstance(snapshot.get("active_zones"), list) else []
    if zone and zone not in zones:
        raise ValueError("Ausgewählte firewalld-Zone ist nicht aktiv")
    return zone


def _deny_hits_management(rules: list[dict]) -> bool:
    web_ports = {
        int(port["port_start"])
        for row in service_port_inventory(default_config_path())
        if row.get("critical")
        for port in row.get("ports", [])
        if port.get("protocol") == "tcp" and port.get("port_start") == port.get("port_end")
    }
    web_ports.add(_current_web_port())
    return any(
        rule["effect"] == "deny"
        and rule["protocol"] == "tcp"
        and any(rule["port_start"] <= port <= rule["port_end"] for port in web_ports)
        for rule in rules
    )


def _test_rules(data: dict) -> tuple[list[dict], str]:
    effect = str(data.get("effect") or "").strip().lower()
    zone = _backend_zone(data)
    service_id = str(data.get("service") or "").strip()
    if service_id:
        service = next((row for row in service_port_inventory(default_config_path()) if row["id"] == service_id), None)
        if service is None:
            raise ValueError("Unbekannter Dienst")
        if service.get("critical") and effect == "deny":
            raise ValueError("Der aktuelle SimpleOffice-Verwaltungsport kann aus der Remote-Weboberfläche nicht gesperrt werden.")
        rules = rules_for_service(service, effect)
        label = service["name"]
    else:
        raw_rules = data.get("rules")
        if isinstance(raw_rules, list) and data.get("effect"):
            raw_rules = [{**rule, "effect": effect} if isinstance(rule, dict) else rule for rule in raw_rules]
        rules = normalize_rules(raw_rules)
        label = "manuelle Regel"
    if zone:
        rules = [{**rule, "zone": zone} for rule in rules]
    if _deny_hits_management(rules):
        raise ValueError("Die Regel würde den SimpleOffice-Verwaltungsport sperren. Dafür ist eine lokale Konsole erforderlich.")
    return rules, label


@bp.get("")
@admin_required
def index():
    return render_template("admin/firewall.html", firewall=firewall_status(default_config_path()))


@api_bp.get("")
@admin_required
def status():
    return jsonify(firewall_status(default_config_path()))


@api_bp.post("/refresh")
@admin_required
def refresh():
    try:
        operation = _store().enqueue("snapshot")
    except (OSError, sqlite3.Error, ValueError):
        return jsonify(error="Firewall-Status konnte nicht zur Aktualisierung vorgemerkt werden."), 503
    return jsonify(operation), 202


@api_bp.get("/operations/<ident>")
@admin_required
def operation(ident):
    try:
        row = _store().operation(ident)
    except (OSError, sqlite3.Error):
        return jsonify(error="Firewall-Aktionsstatus ist nicht lesbar."), 503
    if row is None:
        abort(404)
    return jsonify(row)


@api_bp.post("/test")
@admin_required
def test_change():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="JSON-Objekt erwartet."), 400
    try:
        snapshot = _store().snapshot()
        if snapshot.get("conflict"):
            raise ValueError("UFW und firewalld sind gleichzeitig aktiv. Schreibaktionen sind gesperrt.")
        if not snapshot.get("writable"):
            raise ValueError("Kein unterstützter aktiver Firewall-Manager ist schreibbar.")
        rules, label = _test_rules(data)
        operation = _store().enqueue("test", {"rules": rules})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, sqlite3.Error):
        return jsonify(error="Firewall-Test konnte nicht vorgemerkt werden."), 503
    audit("mini_firewall_test_requested", "service", "firewall", detail={"effect": rules[0]["effect"], "rules": len(rules), "label": label})
    return jsonify(operation), 202


@api_bp.post("/test/<ident>/confirm")
@admin_required
def confirm(ident):
    try:
        test_id = validate_test_id(ident)
        operation = _store().enqueue("confirm", {"test_id": test_id})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, sqlite3.Error):
        return jsonify(error="Firewall-Bestätigung konnte nicht vorgemerkt werden."), 503
    audit("mini_firewall_confirm_requested", "service", "firewall", detail={"test_id": test_id})
    return jsonify(operation), 202


@api_bp.post("/test/<ident>/rollback")
@admin_required
def rollback(ident):
    try:
        test_id = validate_test_id(ident)
        operation = _store().enqueue("rollback", {"test_id": test_id})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, sqlite3.Error):
        return jsonify(error="Firewall-Rollback konnte nicht vorgemerkt werden."), 503
    audit("mini_firewall_rollback_requested", "service", "firewall", detail={"test_id": test_id})
    return jsonify(operation), 202


@api_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
