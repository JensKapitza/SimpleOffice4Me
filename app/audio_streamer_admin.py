"""Admin controls for live audio streaming."""
from __future__ import annotations
import sqlite3
import threading
import platform

import time

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from .access_control import audit, is_admin
from .audio_output_discovery import discover_receiver_outputs, discover_microphone_inputs
from .audio_streamer import manager, receiver_sdp
from .auth import login_required
from .audio_streamer_config import settings as stream_settings, DEFAULTS, default_settings

bp = Blueprint("audio_streamer_admin", __name__, url_prefix="/admin/mini-services/audio/streamer")
_target_scan_lock = threading.Lock()


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def payload() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("JSON-Objekt erwartet")
    return value


def _configuration_error():
    return jsonify({"error": "Ungültige Audio-Konfiguration."}), 400


def _runtime_error():
    return jsonify({"error": "Audio-Dienst ist auf diesem System nicht verfügbar."}), 503


@bp.get("")
@admin_required
def page():
    return render_template("admin/audio_streamer.html", status=manager.status(), settings={name: stream_settings(name) for name in DEFAULTS}, windows_audio=platform.system() == "Windows")


@bp.get("/status")
@admin_required
def status():
    return jsonify(manager.status())


@bp.get("/outputs")
@admin_required
def outputs():
    try:
        result = discover_receiver_outputs()
    except RuntimeError as exc:
        audit(
            "audio_output_discovery_failed",
            "audio_stream",
            "outputs",
            outcome="failure",
            detail={"error_type": type(exc).__name__},
        )
        return _runtime_error()
    audit("audio_output_discovery", "audio_stream", "outputs", detail={"count": len(result)})
    response = {"outputs": result, "count": len(result), "state": "completed", "updated_at": time.time()}
    if platform.system() == "Windows":
        response["message"] = "Systemstandard verfügbar; keine Hardwareprüfung. Das Ausgabegerät wird in Windows gewählt."
    return jsonify(response)


@bp.get("/inputs")
@admin_required
def inputs():
    try:
        result = discover_microphone_inputs(request.args.get("backend", "auto"))
    except ValueError:
        return jsonify(error="Capture-Backend muss auto, pulse, alsa oder dshow sein."), 400
    except (RuntimeError, OSError) as exc:
        audit("audio_input_discovery_failed", "audio_stream", "inputs", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify(error="Keine Mikrofone ermittelt. Audio-Sitzung, Geräte und Zugriffsrechte für PipeWire/PulseAudio, ALSA oder Windows/DirectShow prüfen.", code="input_discovery_failed"), 503
    return jsonify({"inputs": result, "count": len(result), "state": "completed", "updated_at": time.time()})


@bp.post("/sender/start")
@admin_required
def sender_start():
    try:
        data = payload()
        result = manager.configured_start("sender", data)
    except (ValueError, TypeError):
        return _configuration_error()
    except (RuntimeError, OSError) as exc:
        audit(
            "audio_stream_sender_failed",
            "audio_stream",
            "sender",
            outcome="failure",
            detail={"error_type": type(exc).__name__},
        )
        return _runtime_error()
    audit("audio_stream_sender_started", "audio_stream", "sender")
    return jsonify(result), 202


@bp.post("/sender/stop")
@admin_required
def sender_stop():
    manager.stop_sender()
    audit("audio_stream_sender_stopped", "audio_stream", "sender")
    return jsonify({"stopped": True})


@bp.post("/receiver/start")
@admin_required
def receiver_start():
    try:
        data = payload()
        result = manager.configured_start("receiver", data)
    except (ValueError, TypeError):
        return _configuration_error()
    except (RuntimeError, OSError) as exc:
        audit(
            "audio_stream_receiver_failed",
            "audio_stream",
            "receiver",
            outcome="failure",
            detail={"error_type": type(exc).__name__},
        )
        return _runtime_error()
    audit("audio_stream_receiver_started", "audio_stream", "receiver")
    return jsonify(result), 202


@bp.post("/receiver/stop")
@admin_required
def receiver_stop():
    manager.stop_receiver()
    audit("audio_stream_receiver_stopped", "audio_stream", "receiver")
    return jsonify({"stopped": True})


@bp.post("/targets/scan")
@admin_required
def targets_scan():
    from .audio_target_discovery import targets_from_profiles
    from .federation_discovery_lan import discover_lan
    if not _target_scan_lock.acquire(blocking=False):
        return jsonify(error="Empfängersuche läuft bereits. Kurz warten und erneut versuchen."), 409
    try:
        result = discover_lan(current_app.config["DOCUMENT_ROOT"])
        targets = targets_from_profiles(result["peers"])
        response = jsonify(state="completed", targets=targets, count=len(targets),
                           updated_at=time.time(), networks=result["networks"],
                           scope="Aktive SimpleOffice-Desktop-Empfänger im lokalen IPv4-Netz")
        response.headers["Cache-Control"] = "no-store"
        return response
    except (OSError, ValueError, sqlite3.Error) as exc:
        audit("audio_target_scan_failed", "audio_stream", "sender", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify(state="failed", updated_at=time.time(), error="Empfängersuche fehlgeschlagen. Privates IPv4-Netz und Federation-Port prüfen."), 503
    finally:
        _target_scan_lock.release()


@bp.get("/settings")
@admin_required
def settings_get():
    return jsonify({name: stream_settings(name) for name in DEFAULTS})


@bp.post("/<service>/settings")
@admin_required
def settings_save(service):
    if service not in DEFAULTS:
        abort(404)
    try:
        data = payload()
        value = stream_settings(service, {**stream_settings(service), **data})
        if not value["enabled"]:
            getattr(manager, "stop_" + service)()
    except (ValueError, TypeError):
        return _configuration_error()
    audit("audio_stream_settings", "audio_stream", service, detail={"enabled": value["enabled"], "autostart": value["autostart"]})
    return jsonify(value)


@bp.post("/<service>/reset")
@admin_required
def settings_reset(service):
    if service not in DEFAULTS:
        abort(404)
    getattr(manager, "stop_" + service)()
    return jsonify(stream_settings(service, default_settings(service)))


@bp.post("/<service>/restart")
@admin_required
def restart(service):
    if service not in DEFAULTS:
        abort(404)
    try:
        result = manager.configured_start(service, restart=True)
    except (ValueError, TypeError):
        return _configuration_error()
    except (OSError, RuntimeError):
        return _runtime_error()
    audit("audio_stream_restarted", "audio_stream", service)
    return jsonify(result), 202


@bp.get("/receiver.sdp")
@admin_required
def receiver_sdp_download():
    try:
        content = receiver_sdp(int(request.args.get("port") or 5004))
    except (ValueError, TypeError):
        return jsonify({"error": "Ungültiger RTP-Port."}), 400
    return content, 200, {"Content-Type": "application/sdp"}


@bp.errorhandler(sqlite3.Error)
def storage_error(exc):
    audit("audio_storage_failed", "service", "audio-streamer", outcome="failure", detail={"error_type": type(exc).__name__})
    return jsonify(error="Audio-Einstellungen nicht verfügbar. Dateirechte und Datenbank prüfen; anschließend erneut versuchen."), 503
