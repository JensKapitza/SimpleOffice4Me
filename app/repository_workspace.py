"""Public repository workspace with optional session-bound GitHub reads."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from . import repository_workspace_auth as auth
from .repository_workspace_github import WorkspaceError, WorkspaceUnauthorized
from .repository_workspace_store import WorkspaceAuthError

bp = Blueprint("repository_workspace", __name__, url_prefix="/repository-workspace")


def _data():
    from . import repository_workspace_data
    return repository_workspace_data


def _discard_account() -> None:
    previous = auth.disconnect()
    if previous != "public":
        _data().invalidate_cache(previous)


@bp.get("/")
def index():
    return render_template("repository_workspace.html")


@bp.get("/api/status")
def account_status():
    return jsonify(auth.status())


@bp.post("/api/auth/start")
def auth_start():
    return jsonify(auth.start())


@bp.post("/api/auth/poll")
def auth_poll():
    result = auth.poll()
    result["auth"] = auth.status()
    if result.get("status") == "authenticated" and not result["auth"]["authenticated"]:
        result["status"] = "expired"
    return jsonify(result)


@bp.post("/api/auth/logout")
def auth_logout():
    _discard_account()
    return jsonify({"status": "public", "auth": auth.status()})


def _snapshot(refresh: bool = False):
    previous = auth.cache_key()
    try:
        token, cache_key = auth.credentials()
        result = dict(_data().snapshot(token, refresh=refresh, cache_key=cache_key))
        result["auth"] = auth.status()
        # A logout or expired authorization must not publish a completed older
        # account fetch into the new public browser context.
        if token and auth.cache_key() != cache_key:
            _data().invalidate_cache(cache_key)
            return jsonify({"error": "Die GitHub-Sitzung hat sich geändert. Daten erneut abrufen.",
                            "auth": auth.status()}), 401
        return jsonify(result)
    except (WorkspaceUnauthorized, WorkspaceAuthError) as exc:
        if previous != "public":
            _data().invalidate_cache(previous)
        _discard_account()
        return jsonify({"error": str(exc), "auth": auth.status()}), 401


@bp.get("/api/snapshot")
def snapshot():
    return _snapshot()


@bp.post("/api/refresh")
def refresh():
    return _snapshot(refresh=True)


@bp.after_request
def prevent_account_cache(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.vary.add("Cookie")
    return response


@bp.errorhandler(WorkspaceAuthError)
@bp.errorhandler(WorkspaceError)
def provider_error(error):
    if error.status == 401:
        _discard_account()
    response = jsonify({"error": str(error), "auth": auth.status()})
    response.status_code = error.status
    if error.retry_after:
        response.headers["Retry-After"] = str(error.retry_after)
    return response


@bp.errorhandler(Exception)
def workspace_error(error):
    """Never let a read-only view trigger the automatic GitHub issue reporter."""
    if isinstance(error, HTTPException):
        if request.path.startswith(bp.url_prefix + "/api/"):
            return jsonify({"error": "Die Anfrage konnte nicht verarbeitet werden."}), error.code
        return error
    current_app.logger.error("repository_workspace_failure type=%s", type(error).__name__)
    return jsonify({"error": "Die Arbeitszentrale ist vorübergehend nicht verfügbar. Bitte erneut versuchen."}), 500
