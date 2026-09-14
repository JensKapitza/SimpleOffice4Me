"""Admin-only view and command transport for the existing Mini Service owners."""
from __future__ import annotations

import time
import sqlite3
from flask import Blueprint, abort, jsonify, request

from simpleoffice_mini_control import ControlStore, NETWORK_SERVICES
from simpleoffice_mini_core import default_config_path, read_status
from simpleoffice_service_lifecycle import error_detail
from .mini_services_admin import admin_required
from .access_control import audit

bp = Blueprint("mini_services_api", __name__, url_prefix="/api/mini-services")
NAMES = {"dhcp": "DHCP", "dns": "DNS", "tftp": "TFTP / Netzwerkboot", "sip": "SIP", "gateway": "Routing / NAT"}


def _store():
    return ControlStore(default_config_path())


def _catalog():
    status = read_status(default_config_path())
    store = _store()
    preferences = store.preferences()
    states = status.get("services", {})
    rows = []
    for name in NETWORK_SERVICES:
        row = states.get(name, {"id": name, "name": NAMES[name], "state": "unavailable",
                               "health": {"ok": False, "message": "Wartet auf Mini-Services Worker"}})
        row.update(settings=preferences[name], scan=store.scan(name),
                   capabilities=["start", "stop", "restart", "scan", "settings"],
                   owner="mini-services-worker")
        rows.append(row)
    return {"services": rows, "worker": {key: status.get(key) for key in ("state", "pid", "updated_at", "stale", "config_error")}}


@bp.get("")
@admin_required
def index():
    return jsonify(_catalog())


@bp.get("/<service>")
@admin_required
def service_status(service):
    for row in _catalog()["services"]:
        if row["id"] == service:
            return jsonify(row)
    abort(404)


@bp.get("/operations/<ident>")
@admin_required
def operation(ident):
    row = _store().operation(ident)
    if row is None:
        abort(404)
    return jsonify(row)


@bp.post("/<service>/settings")
@admin_required
def settings(service):
    if service not in NETWORK_SERVICES:
        abort(404)
    try:
        value = _store().save_preferences(service, request.get_json(silent=True))
    except (ValueError, TypeError):
        return jsonify(error="Aktiviert und Autostart müssen boolesche Werte sein."), 400
    audit("mini_service_settings", "service", service, detail=value)
    return jsonify(settings=value)


@bp.post("/<service>/<action>")
@admin_required
def action(service, action):
    if service not in NETWORK_SERVICES or action not in {"start", "stop", "restart", "scan"}:
        abort(404)
    store = _store()
    if action == "scan":
        return _scan(store, service)
    if read_status(default_config_path()).get("state") not in {"running", "degraded"}:
        return jsonify(error="Mini-Services Worker ist nicht erreichbar. SimpleOffice oder den Worker starten.", code="worker_unavailable"), 503
    try:
        command = store.enqueue(service, action)
    except (ValueError, sqlite3.OperationalError):
        return jsonify(error="Aktion konnte nicht vorgemerkt werden. Kurz warten und erneut versuchen."), 409
    audit("mini_service_action", "service", service, detail={"action": action, "operation_id": command["id"]})
    return jsonify(command), 202


def _scan(store, service):
    from .network_system_status import network_interfaces
    from simpleoffice_network_boot import list_assets
    started = time.time()
    store.scan(service, {"state": "scanning", "updated_at": started, "count": 0, "targets": []})
    try:
        if service == "tftp":
            targets = list_assets(default_config_path())
        elif service == "sip":
            status = read_status(default_config_path())
            targets = status.get("sip", {}).get("registrations_detail", [])
        else:
            targets = network_interfaces()
        result = {"state": "completed", "updated_at": time.time(), "count": len(targets), "targets": targets,
                  "scope": "Registrierte Telefone" if service == "sip" else "Lokale Bootdateien" if service == "tftp" else "Lokale Netzwerkinterfaces"}
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"state": "failed", "updated_at": time.time(), "count": 0, "targets": [], "error": error_detail(exc)}
        store.scan(service, result)
        return jsonify(result), 503
    store.scan(service, result)
    audit("mini_service_scan", "service", service, detail={"count": len(targets)})
    return jsonify(result)


@bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.errorhandler(sqlite3.Error)
@bp.errorhandler(OSError)
def storage_error(exc):
    audit("mini_service_storage_failed", "service", "control", outcome="failure", detail={"error_type": type(exc).__name__})
    return jsonify(error="Dienststatus oder Steuerdatei ist nicht verfügbar. Dateirechte prüfen und erneut versuchen."), 503
