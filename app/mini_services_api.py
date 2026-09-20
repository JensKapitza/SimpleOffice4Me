"""Admin-only view and command transport for the existing Mini Service owners."""
from __future__ import annotations

import time
import sqlite3
from flask import Blueprint, abort, jsonify, request

from simpleoffice_mini_control import ControlStore, NETWORK_SERVICES
from simpleoffice_mini_core import default_config_path, read_status
from simpleoffice_service_lifecycle import ServiceState, error_detail
from .mini_services_admin import admin_required
from .access_control import audit
from .audio_dependencies import audio_dependencies

bp = Blueprint("mini_services_api", __name__, url_prefix="/api/mini-services")
NAMES = {"dhcp": "DHCP", "dns": "DNS", "tftp": "TFTP / Netzwerkboot", "sip": "SIP", "gateway": "Routing / NAT", "media-renderer": "Media / DLNA"}


def _store():
    return ControlStore(default_config_path())


def _catalog():
    status = read_status(default_config_path())
    store = _store()
    preferences = store.preferences()
    states = status.get("services", {})
    rows = []
    for name in NETWORK_SERVICES:
        row = states.get(name)
        if row is None:
            row = ServiceState(name, NAMES[name], state="unavailable").snapshot(0)
            row["health"] = {"ok": False, "message": "Wartet auf Mini-Services Worker"}
        row.update(settings=preferences[name], scan=store.scan(name),
                   capabilities=["start", "stop", "restart", "scan", "settings"],
                   owner="mini-services-worker")
        rows.append(row)
    from .audio_streamer import manager
    from .audio_streamer_config import settings as stream_settings
    from .audio_output_worker import worker as output_worker
    for key, row in manager.status().items():
        ident = "audio-" + key
        row.update(id=ident, name="Audio-Sender" if key == "sender" else "Audio-Receiver",
                   settings={}, scan=store.scan(ident),
                   capabilities=["start", "stop", "restart", "scan", "settings"])
        try:
            row["settings"] = stream_settings(key)
            row["dependencies"] = audio_dependencies(key, row["settings"])
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            row.update(state="degraded", last_error=error_detail(exc), health={"ok": False, "message": "Audio-Einstellungen nicht lesbar. Speicher und Dateirechte prüfen."})
        rows.append(row)
    row = output_worker.status()
    row.update(settings={}, scan=store.scan("audio-output"),
               capabilities=["start", "stop", "restart", "scan", "settings"])
    try:
        row["settings"] = output_worker.settings()
        row["dependencies"] = audio_dependencies("output", row["settings"])
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        row.update(state="degraded", last_error=error_detail(exc), health={"ok": False, "message": "Audio-Einstellungen nicht lesbar. Speicher und Dateirechte prüfen."})
    rows.append(row)
    from .network_boot_service import status as boot_status
    row = boot_status()
    row["scan"] = store.scan("http-boot")
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
    if service in {"audio-sender", "audio-receiver", "audio-output"}:
        return _audio_action(service, "settings")
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
    if service in {"audio-sender", "audio-receiver", "audio-output"} and action in {"start", "stop", "restart", "scan"}:
        return _audio_action(service, action)
    if service == "http-boot" and action in {"start", "stop", "restart", "scan"}:
        from .network_boot_service import action as boot_action
        store = _store()
        if action == "scan":
            store.scan(service, {"state": "scanning", "updated_at": time.time(), "count": 0, "targets": []})
        try:
            result = boot_action(action)
        except (ValueError, RuntimeError, OSError) as exc:
            if action == "scan":
                store.scan(service, {"state": "failed", "updated_at": time.time(), "count": 0, "targets": [], "error": error_detail(exc)})
            return jsonify(error=error_detail(exc)), 400
        if action == "scan":
            store.scan(service, result)
        audit("mini_service_action", "service", service, detail={"action": action})
        return jsonify(result)
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


def _audio_action(service, action):
    # Delegate to existing owners and validators; no second audio manager.
    from . import audio_streamer_admin, audio_output_admin
    from .audio_output_discovery import discover_microphone_inputs, discover_speaker_outputs, discover_receiver_outputs
    if action == "scan":
        store = _store()
        store.scan(service, {"state": "scanning", "updated_at": time.time(), "count": 0, "targets": []})
        try:
            if service == "audio-sender":
                from .audio_streamer_config import settings as stream_settings
                backend = stream_settings("sender")["backend"]
                devices = discover_microphone_inputs(backend)
            else:
                devices = discover_receiver_outputs() if service == "audio-receiver" else discover_speaker_outputs()
            if service == "audio-output":
                audio_output_admin._store().sync_local_outputs(devices)
            result = {"state": "completed", "updated_at": time.time(), "count": len(devices), "targets": devices, "scope": "Lokale Audiogeräte"}
            if any(device.get("driver") == "FFplay" for device in devices):
                result["scope"] = "Windows-Systemstandard; Hardware nicht geprüft"
            store.scan(service, result)
            return jsonify(result)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            result = {"state": "failed", "updated_at": time.time(), "count": 0, "targets": [], "error": error_detail(exc)}
            store.scan(service, result)
            return jsonify(result), 503
    if service == "audio-output":
        if action == "settings":
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(error="JSON-Objekt erwartet."), 400
            try:
                return jsonify(audio_output_admin.worker.save_settings({**audio_output_admin.worker.settings(), **data}))
            except ValueError:
                return jsonify(error="Audio-Einstellungen prüfen."), 400
        return audio_output_admin.lifecycle(action=action)
    key = service.removeprefix("audio-")
    if action == "settings":
        return audio_streamer_admin.settings_save(service=key)
    if action == "restart":
        return audio_streamer_admin.restart(service=key)
    return getattr(audio_streamer_admin, key + "_" + action)()


def _scan(store, service):
    from .network_system_status import network_interfaces
    from simpleoffice_network_boot import list_assets
    started = time.time()
    store.scan(service, {"state": "scanning", "updated_at": started, "count": 0, "targets": []})
    try:
        if service == "tftp":
            targets = list_assets(default_config_path(), include_hash=False, max_entries=512)
        elif service == "sip":
            status = read_status(default_config_path())
            targets = status.get("sip", {}).get("registrations_detail", [])
        elif service == "media-renderer":
            from .audio_output_discovery import discover_speaker_outputs
            targets = discover_speaker_outputs()
        else:
            targets = network_interfaces()
        result = {"state": "completed", "updated_at": time.time(), "count": len(targets), "targets": targets,
                  "scope": "Registrierte Telefone" if service == "sip" else "Lokale Bootdateien" if service == "tftp" else "Lokale Audioausgänge" if service == "media-renderer" else "Lokale Netzwerkinterfaces"}
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
