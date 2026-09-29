"""Routes for bounded public readiness and administrator health diagnostics."""
from __future__ import annotations

from dataclasses import asdict
from functools import wraps

from flask import Blueprint, abort, current_app, g, jsonify, render_template

from .access_control import is_admin
from .auth import login_required
from .v3_capabilities import enabled
from .v3_health import default_registry, public_summary


bp = Blueprint("v3_health", __name__)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _report() -> dict:
    config = dict(current_app.config)
    capabilities = {
        "v3.jobs": enabled("v3.jobs"),
        "v3.federation": enabled("v3.federation"),
    }
    return default_registry(
        config["DOCUMENT_ROOT"],
        config["DATABASE"],
        config=config,
        capabilities=capabilities,
    ).run(total_timeout_seconds=5.0)


def _detail(report: dict) -> dict:
    payload = public_summary(report)
    payload["checks"] = [asdict(item) for item in report.get("checks", [])]
    return payload


@bp.get("/health/live")
def live():
    if not enabled("v3.health"):
        abort(404)
    return jsonify({"status": "healthy", "live": True})


@bp.get("/health/ready")
def ready():
    if not enabled("v3.health"):
        abort(404)
    report = _report()
    return jsonify(public_summary(report)), 200 if report.get("ready") else 503


@bp.get("/admin/v3/health")
@admin_required
def index():
    feature_enabled = enabled("v3.health")
    report = _report() if feature_enabled else None
    return render_template(
        "admin/v3_health.html",
        enabled=feature_enabled,
        report=report,
    )


@bp.get("/admin/v3/health.json")
@admin_required
def detail():
    if not enabled("v3.health"):
        abort(404)
    return jsonify(_detail(_report()))
