"""Read-only view of a signed-in user's RemoteApp grants."""
from __future__ import annotations

from flask import Blueprint, current_app, g, render_template

from .auth import login_required
from .companion_agent import status
from .remote_app_catalog import RemoteAppCatalog

bp = Blueprint("remote_apps", __name__, url_prefix="/remote/apps")


def _availability(root: str) -> str:
    try:
        agent = status(root, check_process=False)
    except OSError as exc:
        current_app.logger.warning("Remote app catalog status unavailable (%s)", type(exc).__name__)
        return "unknown"

    if agent["platform"] != "windows":
        return "unsupported"
    if not agent["installed"]:
        return "missing"
    if not agent["running"]:
        return "offline"
    if agent["stop_pending"]:
        return "stopping"
    return "active"


@bp.after_request
def _private_catalog(response):
    response.cache_control.private = True
    response.cache_control.no_store = True
    return response


@bp.get("")
@login_required
def index():
    root = current_app.config["DOCUMENT_ROOT"]
    grants = RemoteAppCatalog(root).visible_for(g.user["id"])
    apps = []
    for row in grants:
        label = row.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        description = row.get("description")
        apps.append({
            "label": label,
            "description": description if isinstance(description, str) else "",
            "favorite": row.get("favorite") is True,
        })
    return render_template("remote/apps.html", apps=apps, availability=_availability(root))
