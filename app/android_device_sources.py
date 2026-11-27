"""Authorize the native APK's local, read-only device-source overview."""

from flask import Blueprint, abort, current_app, g, jsonify, make_response, render_template, request

from .access_control import has_feature
from .auth import login_required
from .calendar_collections import CalendarCollections
from .android_managed_snapshot import BIRTHDAYS, calendar_rows, contact_rows, principal_id
from .google_drive_admin import ANDROID_USER_AGENT_TOKEN

bp = Blueprint("android_device_sources", __name__, url_prefix="/android/device-sources")
SOURCES = ("calendar", "contacts")


def _local_apk_request() -> bool:
    return (
        request.remote_addr == "127.0.0.1"
        and request.host == "127.0.0.1:8765"
        and not request.headers.get("Forwarded")
        and not request.headers.get("X-Forwarded-For")
        and not request.headers.get("X-Forwarded-Host")
        and not request.headers.get("X-Forwarded-Proto")
        and ANDROID_USER_AGENT_TOKEN in request.user_agent.string
    )


@bp.app_context_processor
def _source_context():
    user = getattr(g, "user", None)
    return {"show_android_device_sources": (
        user is not None
        and _local_apk_request()
        and any(has_feature(user, source) for source in SOURCES)
    )}


@bp.get("/")
@login_required
def index():
    local_apk = _local_apk_request()
    allowed = [source for source in SOURCES if has_feature(g.user, source)] if local_apk else []
    if local_apk and not allowed:
        abort(403)
    choices = []
    if "calendar" in allowed:
        choices = CalendarCollections(current_app.config["DOCUMENT_ROOT"]).calendars(str(g.user["username"]))
        if "contacts" in allowed:
            choices.append({"calendar_id": BIRTHDAYS, "name": "SimpleOffice Geburtstage"})
    response = make_response(render_template("android/device_sources.html", sources=allowed, calendars=choices))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/authorize/<source>")
@login_required
def authorize(source):
    # The normal app-wide browser mutation guard checks the current session's
    # CSRF token before this handler. No source record is accepted or returned.
    if not _local_apk_request() or source not in SOURCES:
        abort(404)
    if not has_feature(g.user, source):
        abort(403)
    if (request.content_length or 0) != 0 or request.headers.get("Transfer-Encoding"):
        abort(400)
    response = make_response("", 204)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/managed/<source>")
@login_required
def managed_snapshot(source):
    if not _local_apk_request() or source not in SOURCES:
        abort(404)
    if not has_feature(g.user, source):
        abort(403)
    if request.headers.get("Transfer-Encoding") or (request.content_length or 0) > 4096:
        abort(413)
    if not request.is_json or not request.content_length:
        abort(400)
    data = request.get_json(silent=True)
    if type(data) is not dict or set(data) != {"calendar_ids"} or type(data["calendar_ids"]) is not list:
        abort(400)
    selected = data["calendar_ids"]
    if len(selected) > 8 or any(type(i) is not str or len(i) > 63 for i in selected) or (source == "contacts" and selected):
        abort(400)
    root = current_app.config["DOCUMENT_ROOT"]
    actor = str(g.user["username"])
    try:
        rows = (contact_rows(root, actor) if source == "contacts" else
                calendar_rows(root, actor, selected, has_feature(g.user, "contacts")))
    except OverflowError:
        abort(413)
    except (ValueError, KeyError):
        abort(422)
    response = jsonify({"principal": principal_id(g.user), "rows": rows})
    response.headers["Cache-Control"] = "private, no-store"
    return response
