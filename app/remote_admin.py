"""Administrator UI for the optional SimpleOffice Remote companion features."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit, is_admin
from .auth import login_required
from .companion_agent import install, start, status, stop
from .remote_app_catalog import RemoteAppCatalog
from .translations import translate

bp = Blueprint("remote_admin", __name__, url_prefix="/admin/remote")


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _catalog() -> RemoteAppCatalog:
    return RemoteAppCatalog(_root())


def _redirect():
    return redirect(url_for("remote_admin.index"))


@bp.get("")
@admin_required
def index():
    return render_template("admin/remote.html", agent=status(_root()), apps=_catalog().all())


@bp.post("/agent/install")
@admin_required
def install_agent():
    result = install(_root())
    audit("remote_agent_installed", "service", "companion-agent", detail={"platform": result["platform"]})
    flash(translate(g.language, "remote.agent.prepared"))
    return _redirect()


@bp.post("/agent/start")
@admin_required
def start_agent():
    try:
        result = start(_root())
    except Exception as exc:
        audit("remote_agent_started", "service", "companion-agent", outcome="failure", detail={"error_type": type(exc).__name__})
        flash(translate(g.language, "remote.agent.start.failed").format(exc=exc))
    else:
        audit("remote_agent_started", "service", "companion-agent", detail={"platform": result["platform"]})
        flash(translate(g.language, "remote.agent.started"))
    return _redirect()


@bp.post("/agent/stop")
@admin_required
def stop_agent():
    result = stop(_root())
    audit("remote_agent_stopped", "service", "companion-agent", detail={"platform": result["platform"]})
    flash(translate(g.language, "remote.agent.stopped"))
    return _redirect()


@bp.post("/apps")
@admin_required
def publish_app():
    try:
        row = _catalog().publish(
            label=request.form.get("label", ""),
            executable=request.form.get("executable", ""),
            description=request.form.get("description", ""),
            favorite=request.form.get("favorite") == "1",
        )
    except Exception as exc:
        flash(translate(g.language, "remote.app.publish.failed").format(exc=exc))
    else:
        audit("remote_app_published", "remote_app", row["app_id"], detail={"label": row["label"]})
        flash(translate(g.language, "remote.app.published").format(label=row['label']))
    return _redirect()


@bp.post("/apps/<app_id>/remove")
@admin_required
def remove_app(app_id: str):
    try:
        row = _catalog().remove(app_id)
    except Exception as exc:
        flash(translate(g.language, "remote.app.remove.failed").format(exc=exc))
    else:
        audit("remote_app_removed", "remote_app", row["app_id"], detail={"label": row["label"]})
        flash(translate(g.language, "remote.app.removed").format(label=row['label']))
    return _redirect()
