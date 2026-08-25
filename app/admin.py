"""Administrator-only account controls and safe operational diagnostics."""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .access_control import FEATURES, audit, is_admin, permissions_for, utc_now
from .auth import login_required
from .db import get_db
from .request_audit import audit_mutation_response


bp = Blueprint("admin", __name__, url_prefix="/admin")
# Blueprint.after_app_request applies to the whole Flask application.  Keeping
# the hook registration here avoids per-route audit boilerplate while the audit
# implementation itself remains in the dedicated request_audit module.
bp.after_app_request(audit_mutation_response)


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


@bp.get("/users")
@admin_required
def users():
    rows = get_db().execute(
        "SELECT * FROM user ORDER BY is_admin DESC, username COLLATE NOCASE"
    ).fetchall()
    return render_template(
        "admin/users.html", users=rows, features=FEATURES,
        permissions={row["id"]: permissions_for(row["id"]) for row in rows},
    )


@bp.post("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    db = get_db()
    target = db.execute("SELECT * FROM user WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        abort(404)
    disabled = request.form.get("is_disabled") == "1"
    administrator = request.form.get("is_admin") == "1"
    if target["id"] == g.user["id"] and (disabled or not administrator):
        flash("Das eigene aktive Administratorkonto kann nicht gesperrt oder herabgestuft werden.")
        return redirect(url_for("admin.users"))
    if target["is_admin"] and (disabled or not administrator):
        remaining = db.execute(
            "SELECT COUNT(*) FROM user WHERE is_admin = 1 AND is_disabled = 0 AND id <> ?",
            (user_id,),
        ).fetchone()[0]
        if remaining == 0:
            flash("Mindestens ein aktiver Administrator muss erhalten bleiben.")
            return redirect(url_for("admin.users"))

    before = {"admin": bool(target["is_admin"]), "disabled": bool(target["is_disabled"])}
    db.execute(
        """UPDATE user SET is_admin = ?, is_disabled = ?, auth_version = auth_version + 1,
               updated_at = ? WHERE id = ?""",
        (int(administrator), int(disabled), utc_now(), user_id),
    )
    for feature in FEATURES:
        enabled = request.form.get(f"feature_{feature}") == "1"
        db.execute(
            """INSERT INTO user_permission(user_id, feature, enabled, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, feature) DO UPDATE SET
                   enabled=excluded.enabled, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
            (user_id, feature, int(enabled), utc_now(), g.user["id"]),
        )
    db.commit()
    audit(
        "user_access_updated", "user", str(user_id),
        detail={"before": before, "admin": administrator, "disabled": disabled,
                "features": {feature: request.form.get(f"feature_{feature}") == "1" for feature in FEATURES}},
    )
    flash(f"Rechte für {target['username']} gespeichert; vorhandene Sitzungen wurden beendet.")
    return redirect(url_for("admin.users"))


@bp.get("/logs")
@admin_required
def logs():
    try:
        page = max(1, int(request.args.get("page", "1")))
        event_page = max(1, int(request.args.get("event_page", "1")))
    except ValueError:
        page = event_page = 1
    limit = 50
    offset = (page - 1) * limit
    event_offset = (event_page - 1) * limit
    errors = get_db().execute(
        "SELECT * FROM application_error ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
        (limit + 1, offset),
    ).fetchall()

    event_filters = {
        key: request.args.get(key, "").strip()
        for key in ("actor", "action", "outcome", "from_at", "to_at")
    }
    where: list[str] = []
    parameters: list[object] = []
    if event_filters["actor"]:
        where.append("actor_name LIKE ?")
        parameters.append(f"%{event_filters['actor']}%")
    if event_filters["action"]:
        where.append("action LIKE ?")
        parameters.append(f"%{event_filters['action']}%")
    if event_filters["outcome"]:
        where.append("outcome = ?")
        parameters.append(event_filters["outcome"])
    if event_filters["from_at"]:
        where.append("occurred_at >= ?")
        parameters.append(event_filters["from_at"])
    if event_filters["to_at"]:
        where.append("occurred_at < datetime(?, '+1 day')")
        parameters.append(event_filters["to_at"])
    predicate = f" WHERE {' AND '.join(where)}" if where else ""
    events = get_db().execute(
        f"SELECT * FROM security_event{predicate} ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
        (*parameters, limit + 1, event_offset),
    ).fetchall()
    return render_template(
        "admin/logs.html", errors=errors[:limit], events=events[:limit],
        page=page, has_next=len(errors) > limit,
        event_page=event_page, event_has_next=len(events) > limit,
        event_filters=event_filters,
    )


@bp.post("/errors/<int:error_id>/resolve")
@admin_required
def resolve_error(error_id: int):
    result = get_db().execute(
        "UPDATE application_error SET resolved_at = ?, resolved_by = ? WHERE id = ? AND resolved_at IS NULL",
        (utc_now(), g.user["id"], error_id),
    )
    get_db().commit()
    if result.rowcount:
        audit("application_error_resolved", "application_error", str(error_id))
    return redirect(url_for("admin.logs"))
