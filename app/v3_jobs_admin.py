"""Minimal administrator view for the optional V3 background queue."""
from __future__ import annotations
from functools import wraps
from flask import Blueprint, abort, current_app, g, redirect, render_template, url_for

from .access_control import is_admin
from .auth import login_required
from .v3_capabilities import enabled
from .v3_jobs import JobStore

bp=Blueprint("v3_jobs_admin",__name__,url_prefix="/admin/v3/jobs")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args,**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(*args,**kwargs)
    return wrapped


def _store():
    return JobStore(current_app.config["DOCUMENT_ROOT"])


@bp.get("")
@admin_required
def index():
    if not enabled("v3.jobs"):
        return render_template("admin/v3_jobs.html",enabled=False,jobs=[],metrics={})
    return render_template("admin/v3_jobs.html",enabled=True,jobs=_store().list(limit=200),metrics=_store().metrics())


@bp.post("/<job_id>/retry")
@admin_required
def retry(job_id):
    if not enabled("v3.jobs"):
        abort(404)
    _store().retry_failed(job_id)
    return redirect(url_for("v3_jobs_admin.index"))
