"""Admin controls for live audio streaming."""
from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, render_template, request

from .access_control import audit, is_admin
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
