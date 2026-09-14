"""Admin controls for live audio streaming."""
from __future__ import annotations

import time

from flask import Blueprint, abort, g, jsonify, render_template, request

from .access_control import audit, is_admin
from .audio_output_discovery import discover_speaker_outputs, discover_microphone_inputs
from .audio_streamer import manager, receiver_sdp
from .auth import login_required

bp = Blueprint("audio_streamer_admin", __name__, url_prefix="/admin/mini-services/audio/streamer")


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
    return render_template("admin/audio_streamer.html", status=manager.status())


@bp.get("/status")
@admin_required
def status():
    return jsonify(manager.status())


@bp.get("/outputs")
@admin_required
def outputs():
    try:
        result = discover_speaker_outputs()
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
    return jsonify({"outputs": result, "count": len(result), "state": "completed", "updated_at": time.time()})


@bp.get("/inputs")
@admin_required
def inputs():
    try:
        result = discover_microphone_inputs()
    except RuntimeError as exc:
        audit("audio_input_discovery_failed", "audio_stream", "inputs", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify(error="Keine Mikrofone ermittelt. Audio-Sitzung und PipeWire/PulseAudio prüfen; bei ALSA die Quelle manuell wählen.", code="input_discovery_failed"), 503
    return jsonify({"inputs": result, "count": len(result), "state": "completed", "updated_at": time.time()})


@bp.post("/sender/start")
@admin_required
def sender_start():
    try:
        data = payload()
        result = manager.start_sender(
            source=str(data.get("source") or "default"),
            backend=str(data.get("backend") or "pulse"),
            destinations=data.get("destinations"),
            bitrate_kbps=int(data.get("bitrate_kbps") or 64),
        )
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
        result = manager.start_receiver(
            port=int(data.get("port") or 5004),
            speaker_devices=data.get("speaker_devices") or [],
            virtual_microphone=bool(data.get("virtual_microphone", True)),
            virtual_sink=str(data.get("virtual_sink") or "simpleoffice_stream"),
        )
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


@bp.get("/receiver.sdp")
@admin_required
def receiver_sdp_download():
    try:
        content = receiver_sdp(int(request.args.get("port") or 5004))
    except (ValueError, TypeError):
        return jsonify({"error": "Ungültiger RTP-Port."}), 400
    return content, 200, {"Content-Type": "application/sdp"}
