"""Administrator endpoints for network audio outputs and announcements."""
from __future__ import annotations
import sqlite3

import time

from flask import Blueprint, abort, g, jsonify, render_template, request

from .access_control import audit, is_admin
from .audio_output_store import AudioOutputStore, DEFAULT_PRESETS
from .audio_output_discovery import discover_speaker_outputs
from .audio_output_worker import worker
from .auth import login_required
from .mini_services import default_config_path

bp = Blueprint("audio_output_admin", __name__, url_prefix="/admin/mini-services/audio")


def _store() -> AudioOutputStore:
    return AudioOutputStore(default_config_path().parent / "audio")


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _json() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("JSON-Objekt erwartet")
    return value


@bp.get("")
@admin_required
def status():
    store = _store()
    return jsonify({"outputs": store.outputs(), "groups": store.groups(), "presets": DEFAULT_PRESETS, "queue": store.history(100), "service": worker.status()})


@bp.get("/ui")
@admin_required
def index():
    store = _store()
    return render_template(
        "admin/audio_output.html",
        outputs=store.outputs(),
        groups=store.groups(),
        presets=DEFAULT_PRESETS,
        queue=store.history(100),
        service=worker.status(),
    )


@bp.post("/outputs")
@admin_required
def register_output():
    try:
        data = _json()
        result = _store().register_output(
            data.get("node_id", ""), data.get("output_id", ""), data.get("name", ""),
            device=data.get("device", ""), channels=data.get("channels", 2),
            online=data.get("online", True), volume=data.get("volume", 100), transport=data.get("transport"),
        )
    except (ValueError, TypeError) as exc:
        audit("audio_output_validation_failed", "audio_output", "register", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify({"error": "Ausgangsdaten sind ungültig."}), 400
    audit("audio_output_registered", "audio_output", f"{result['node_id']}:{result['output_id']}")
    return jsonify(result), 201


@bp.post("/scan")
@admin_required
def scan():
    try:
        devices = discover_speaker_outputs()
        outputs = _store().sync_local_outputs(devices)
    except (RuntimeError, OSError) as exc:
        audit("audio_output_scan_failed", "audio_output", "local", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify(error="Lokale Ausgänge nicht erreichbar. Audio-Sitzung und PipeWire/PulseAudio prüfen.", state="failed", updated_at=time.time()), 503
    audit("audio_output_scan", "audio_output", "local", detail={"count": len(devices)})
    return jsonify(outputs=outputs, state="completed", count=len(devices), updated_at=time.time(), scope="Lokale PipeWire/PulseAudio-Ausgänge")


@bp.post("/<action>")
@admin_required
def lifecycle(action):
    if action not in {"start", "stop", "restart"}:
        abort(404)
    try:
        if action in {"stop", "restart"}:
            worker.stop()
        if action in {"start", "restart"}:
            worker.start()
    except RuntimeError:
        return jsonify(error="Audio-Ausgabe wird noch beendet. Status aktualisieren und erneut versuchen."), 409
    audit("audio_output_lifecycle", "audio_output", "local", detail={"action": action})
    return jsonify(worker.status())


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def output_settings():
    try:
        value = worker.save_settings(_json()) if request.method == "POST" else worker.settings()
    except (ValueError, TypeError):
        return jsonify(error="Aktiviert/Autostart und Wiederholungsanzahl prüfen."), 400
    return jsonify(value)


@bp.post("/queue/<int:ident>/cancel")
@admin_required
def cancel(ident):
    changed = _store().cancel(ident)
    if not changed:
        return jsonify(error="Nur wartende Aufträge können einzeln abgebrochen werden; aktive Wiedergabe über Stop beenden."), 409
    audit("audio_output_cancelled", "audio_output", str(ident))
    return jsonify(cancelled=True)


@bp.post("/groups")
@admin_required
def set_group():
    try:
        data = _json()
        result = _store().set_group(data.get("group_id", ""), data.get("name", ""), data.get("members", []))
    except (ValueError, TypeError) as exc:
        audit("audio_output_validation_failed", "audio_group", "update", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify({"error": "Audiogruppe ist ungültig."}), 400
    audit("audio_group_updated", "audio_group", result["group_id"])
    return jsonify(result)


@bp.post("/say")
@admin_required
def say():
    try:
        data = _json()
        result = _store().queue_tts(
            data.get("text", ""), data.get("targets", []), priority=data.get("priority", 50),
            voice=data.get("voice", "de_DE"), source="admin",
        )
    except (ValueError, TypeError) as exc:
        audit("audio_output_validation_failed", "audio_announcement", "tts", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify({"error": "Ansageauftrag ist ungültig."}), 400
    audit("audio_tts_queued", "audio_announcement", str(result["id"]), detail={"targets": result["targets"]})
    return jsonify(result), 202


@bp.post("/sound")
@admin_required
def sound():
    try:
        data = _json()
        result = _store().queue_sound(data.get("preset", ""), data.get("targets", []), priority=data.get("priority"), source="admin")
    except (ValueError, TypeError) as exc:
        audit("audio_output_validation_failed", "audio_announcement", "sound", outcome="failure", detail={"error_type": type(exc).__name__})
        return jsonify({"error": "Soundauftrag ist ungültig."}), 400
    audit("audio_sound_queued", "audio_announcement", str(result["id"]), detail={"targets": result["targets"]})
    return jsonify(result), 202


@bp.errorhandler(sqlite3.Error)
@bp.errorhandler(OSError)
def storage_error(exc):
    audit("audio_storage_failed", "service", "audio-output", outcome="failure", detail={"error_type": type(exc).__name__})
    return jsonify(error="Audio-Speicher nicht verfügbar. Dateirechte und Datenbank prüfen; anschließend erneut versuchen."), 503
