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


@bp.get("")
@admin_required
def page():
    return render_template("admin/audio_streamer.html", status=manager.status())


@bp.get("/status")
@admin_required
def status():
    return jsonify(manager.status())
