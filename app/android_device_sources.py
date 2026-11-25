"""Authorize the native APK's local, read-only device-source overview."""

from flask import Blueprint, abort, g, make_response, render_template, request

from .access_control import has_feature
from .auth import login_required
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
    if not _local_apk_request():
        abort(404)
    allowed = [source for source in SOURCES if has_feature(g.user, source)]
    if not allowed:
        abort(403)
    response = make_response(render_template("android/device_sources.html", sources=allowed))
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
