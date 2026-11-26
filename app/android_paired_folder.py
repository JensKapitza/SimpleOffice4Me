"""LAN-only, ordinarily authenticated PC UI. No native owner operations are HTTP routes."""
from __future__ import annotations

from uuid import uuid4

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, session, url_for

from .access_control import has_feature
from .android_paired_folder_session import folder_profile, paired_folder_session, require
from .auth import login_required
from .device_source_grants import GrantDenied

bp = Blueprint("android_paired_folder", __name__, url_prefix="/android/pc-folders")
_FIELDS = {
    "begin": {"public_key"},
    "prove": {"challenge", "signature"},
    "status": set(),
    "start": {"query"},
    "read": {"challenge", "signature", "query"},
}
_MAX_BODY = 2048


def _trusted_lan() -> bool:
    return request.is_secure and request.environ.get("simpleoffice.lan_https") is True


def pc_folder_url() -> str:
    """Only the APK startup registers this blueprint; stock Flask has no link or route."""
    return url_for("android_paired_folder.index") if bp.name in current_app.blueprints and _trusted_lan() else ""


def _lan_only() -> None:
    if not _trusted_lan():
        abort(404)
    if not has_feature(g.user, "documents"):
        abort(403)


@bp.after_request
def privacy(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.get("")
@login_required
def index():
    _lan_only()
    if "android_pc_folder_browser" not in session:
        session["android_pc_folder_browser"] = uuid4().hex
    return render_template("android/paired_folder_pc.html", pc_url=url_for(".index"))


def _hex(value, length: int) -> bytes:
    require(isinstance(value, str) and len(value) == length * 2
            and all(char in "0123456789abcdef" for char in value))
    return bytes.fromhex(value)


def _query(value) -> str:
    require(isinstance(value, str) and len(value) <= 120 and value == value.strip())
    require(not value or (len(value) >= 2 and value.isprintable()))
    return value


@bp.post("/<operation>")
@login_required
def action(operation):
    _lan_only()
    if operation not in _FIELDS:
        abort(404)
    if not request.is_json or not request.content_length or not 0 < request.content_length <= _MAX_BODY:
        abort(400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != _FIELDS[operation]:
        abort(400)
    try:
        tab = session.get("android_pc_folder_browser", "")
        require(isinstance(tab, str) and len(tab) == 32)
        user = int(g.user["id"])
        caller = (folder_profile(current_app.secret_key, user), user, tab)
        if operation == "status":
            return jsonify(status=paired_folder_session.pc_status(*caller))
        if operation == "begin":
            challenge = paired_folder_session.begin(*caller, _hex(body["public_key"], 65))
            return jsonify(challenge=challenge.id, message=challenge.message.decode("ascii"))
        if operation == "prove":
            _hex(body["challenge"], 16)
            paired_folder_session.prove(*caller, body["challenge"], _hex(body["signature"], 64))
            return jsonify(ok=True)
        query = _query(body["query"])
        if operation == "start":
            challenge = paired_folder_session.start_read(*caller, query)
            return jsonify(challenge=challenge.id, message=challenge.message.decode("ascii"))
        _hex(body["challenge"], 16)
        return jsonify(paired_folder_session.read(*caller, body["challenge"], _hex(body["signature"], 64), query))
    except GrantDenied:
        return jsonify(error="not_available"), 403
