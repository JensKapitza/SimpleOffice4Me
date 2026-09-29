"""Combined task/calendar workboard UI without changing native standards."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .v3_capabilities import enabled
from .v3_workboard import WorkboardService


bp = Blueprint("v3_workboard", __name__, url_prefix="/workboard-v3")


def _window() -> tuple[datetime, datetime, int]:
    raw = request.args.get("date", "").strip()
    try:
        selected = date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        selected = date.today()
    try:
        days = max(1, min(31, int(request.args.get("days", "7"))))
    except ValueError:
        days = 7
    lower = datetime.combine(selected, time.min)
    return lower, lower + timedelta(days=days), days


@bp.get("")
@login_required
def index():
    lower, upper, days = _window()
    actor = str(g.user["username"])
    if not enabled("v3.workboard"):
        return render_template(
            "work/v3_workboard.html",
            enabled=False,
            items=[],
            selected=lower.date().isoformat(),
            days=days,
        )
    service = WorkboardService(current_app.config["DOCUMENT_ROOT"])
    items = service.agenda(
        actor,
        lower,
        upper,
        project_id=request.args.get("project_id", ""),
        contact_id=request.args.get("contact_id", ""),
        status=request.args.get("status", ""),
    )
    return render_template(
        "work/v3_workboard.html",
        enabled=True,
        items=items,
        selected=lower.date().isoformat(),
        days=days,
    )


@bp.post("/<kind>/<native_id>/move")
@login_required
def move(kind, native_id):
    if not enabled("v3.workboard"):
        abort(404)
    try:
        WorkboardService(current_app.config["DOCUMENT_ROOT"]).move(
            kind,
            native_id,
            str(g.user["username"]),
            start=request.form.get("start", ""),
            end=request.form.get("end", ""),
        )
        flash("Planung aktualisiert.")
    except (LookupError, PermissionError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("v3_workboard.index"))
