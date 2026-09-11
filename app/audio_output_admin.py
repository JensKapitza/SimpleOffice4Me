"""Administrator endpoints for network audio outputs and announcements."""
from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, request

from .access_control import audit, is_admin
from .audio_output_store import AudioOutputStore, DEFAULT_PRESETS
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
    return jsonify({"outputs": store.outputs(), "groups": store.groups(), "presets": DEFAULT_PRESETS, "queue": store.pending(100)})


@bp.post("/outputs")
@admin_required
def register_output():
    try:
        data = _json()
        result = _store().register_output(
            data.get("node_id", ""), data.get("output_id", ""), data.get("name", ""),
            device=data.get("device", ""), channels=data.get("channels", 2),
            online=data.get("online", True), volume=data.get("volume", 100),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    audit("audio_output_registered", "audio_output", f"{result['node_id']}:{result['output_id']}")
    return jsonify(result), 201


@bp.post("/groups")
@admin_required
def set_group():
    try:
        data = _json()
        result = _store().set_group(data.get("group_id", ""), data.get("name", ""), data.get("members", []))
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
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
        return jsonify({"error": str(exc)}), 400
    audit("audio_tts_queued", "audio_announcement", str(result["id"]), detail={"targets": result["targets"]})
    return jsonify(result), 202


@bp.post("/sound")
@admin_required
def sound():
    try:
        data = _json()
        result = _store().queue_sound(data.get("preset", ""), data.get("targets", []), priority=data.get("priority"), source="admin")
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    audit("audio_sound_queued", "audio_announcement", str(result["id"]), detail={"targets": result["targets"]})
    return jsonify(result), 202
