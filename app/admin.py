"""Administrator-only account controls and safe operational diagnostics."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from .access_control import FEATURES, activity_for, audit, is_admin, permissions_for, safe_delta, utc_now
from .auth import login_required
from .db import get_db
from .request_audit import audit_mutation_response
from .system_identity import system_info

bp = Blueprint("admin", __name__, url_prefix="/admin")
bp.after_app_request(audit_mutation_response)

AUDIT_FILTER_KEYS = (
    "q", "actor", "action", "target_type", "target_id", "outcome",
    "request_id", "client_ip", "from_at", "to_at",
)


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _audit_filters() -> dict[str, str]:
    return {key: request.args.get(key, "").strip()[:300] for key in AUDIT_FILTER_KEYS}


def _audit_query(filters: dict[str, str]) -> tuple[str, list[object]]:
    where: list[str] = []
    parameters: list[object] = []
    if filters["q"]:
        where.append("(actor_name LIKE ? OR action LIKE ? OR target_type LIKE ? OR target_id LIKE ? OR detail LIKE ?)")
        needle = f"%{filters['q']}%"
        parameters.extend([needle] * 5)
    if filters["actor"]:
        where.append("actor_name LIKE ?"); parameters.append(f"%{filters['actor']}%")
    if filters["action"]:
        where.append("action LIKE ?"); parameters.append(f"%{filters['action']}%")
    if filters["target_type"]:
        where.append("target_type = ?"); parameters.append(filters["target_type"])
    if filters["target_id"]:
        where.append("target_id LIKE ?"); parameters.append(f"%{filters['target_id']}%")
    if filters["outcome"]:
        where.append("outcome = ?"); parameters.append(filters["outcome"])
    if filters["request_id"]:
        where.append("detail LIKE ?"); parameters.append(f'%"request_id": "%{filters["request_id"]}%"%')
    if filters["client_ip"]:
        where.append("detail LIKE ?"); parameters.append(f'%"client_ip": "%{filters["client_ip"]}%"%')
    if filters["from_at"]:
        where.append("occurred_at >= ?"); parameters.append(filters["from_at"])
    if filters["to_at"]:
        where.append("occurred_at < datetime(?, '+1 day')"); parameters.append(filters["to_at"])
    return (f" WHERE {' AND '.join(where)}" if where else ""), parameters


def _event_detail(row) -> dict:
    try:
        value = json.loads(row["detail"] or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _export_filename(extension: str) -> str:
    return f"simpleoffice-audit-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')}.{extension}"


@bp.get("/users")
@admin_required
def users():
    rows = get_db().execute("SELECT * FROM user ORDER BY is_admin DESC, username COLLATE NOCASE").fetchall()
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
            "SELECT COUNT(*) FROM user WHERE is_admin = 1 AND is_disabled = 0 AND id <> ?", (user_id,)
        ).fetchone()[0]
        if remaining == 0:
            flash("Mindestens ein aktiver Administrator muss erhalten bleiben.")
            return redirect(url_for("admin.users"))

    before = {
        "admin": bool(target["is_admin"]),
        "disabled": bool(target["is_disabled"]),
        **{f"feature:{key}": value for key, value in permissions_for(user_id).items()},
    }
    requested_features = {feature: request.form.get(f"feature_{feature}") == "1" for feature in FEATURES}
    db.execute(
        """UPDATE user SET is_admin = ?, is_disabled = ?, auth_version = auth_version + 1,
               updated_at = ? WHERE id = ?""",
        (int(administrator), int(disabled), utc_now(), user_id),
    )
    for feature, enabled in requested_features.items():
        db.execute(
            """INSERT INTO user_permission(user_id, feature, enabled, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, feature) DO UPDATE SET
                   enabled=excluded.enabled, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
            (user_id, feature, int(enabled), utc_now(), g.user["id"]),
        )
    db.commit()
    after = {
        "admin": administrator,
        "disabled": disabled,
        **{f"feature:{key}": value for key, value in requested_features.items()},
    }
    audit(
        "user_access_updated", "user", str(user_id),
        detail={"username": target["username"], "changes": safe_delta(before, after)},
    )
    flash(f"Rechte für {target['username']} gespeichert; vorhandene Sitzungen wurden beendet.")
    return redirect(url_for("admin.users"))


@bp.get("/activity")
@admin_required
def activity():
    target_type = request.args.get("target_type", "").strip()[:120]
    target_id = request.args.get("target_id", "").strip()[:300]
    if not target_type or not target_id:
        abort(400, description="target_type und target_id sind erforderlich")
    events = activity_for(target_type, target_id)
    return render_template("admin/activity.html", events=events, target_type=target_type, target_id=target_id)


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
        "SELECT * FROM application_error ORDER BY occurred_at DESC LIMIT ? OFFSET ?", (limit + 1, offset)
    ).fetchall()
    event_filters = _audit_filters()
    predicate, parameters = _audit_query(event_filters)
    events = get_db().execute(
        f"SELECT * FROM security_event{predicate} ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
        (*parameters, limit + 1, event_offset),
    ).fetchall()
    return render_template(
        "admin/logs.html", errors=errors[:limit], events=events[:limit], page=page,
        has_next=len(errors) > limit, event_page=event_page, event_has_next=len(events) > limit,
        event_filters=event_filters, system=system_info(include_request=True),
    )


@bp.get("/logs/export")
@admin_required
def export_logs():
    export_format = request.args.get("format", "txt").strip().casefold()
    if export_format not in {"txt", "csv"}:
        abort(400, description="format muss txt oder csv sein")
    filters = _audit_filters()
    predicate, parameters = _audit_query(filters)
    rows = get_db().execute(
        f"SELECT * FROM security_event{predicate} ORDER BY occurred_at DESC LIMIT 20000", parameters
    ).fetchall()
    info = system_info(include_request=True)
    exported_at = utc_now()
    active_filters = {key: value for key, value in filters.items() if value}
    audit(
        "audit_exported", "audit", export_format,
        detail={"format": export_format, "rows": len(rows), "filters": active_filters,
                "application_id": info["application_id"], "request_id": info.get("request_id", "")},
    )

    if export_format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([
            "record_type", "occurred_at", "actor", "action", "target_type", "target_id", "outcome",
            "request_id", "application_id", "server_name", "client_ip", "user_agent", "method", "endpoint",
            "status", "detail_json",
        ])
        writer.writerow([
            "system", exported_at, g.user["username"], "audit_export", "system", info["application_id"], "success",
            info.get("request_id", ""), info["application_id"], info["server_name"], info.get("client_ip", ""),
            info.get("user_agent", ""), "GET", "admin.export_logs", "200",
            json.dumps({"system": info, "filters": active_filters}, ensure_ascii=False, sort_keys=True),
        ])
        for row in rows:
            detail = _event_detail(row)
            writer.writerow([
                "event", row["occurred_at"], row["actor_name"] or "System", row["action"], row["target_type"],
                row["target_id"], row["outcome"], detail.get("request_id", ""), detail.get("application_id", ""),
                detail.get("server_name", ""), detail.get("client_ip", ""), detail.get("user_agent", ""),
                detail.get("method", ""), detail.get("endpoint", ""), detail.get("status", ""), row["detail"] or "{}",
            ])
        response = Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = f'attachment; filename="{_export_filename("csv")}"'
        return response

    lines = [
        "SimpleOffice4Me Audit-Export", "=" * 72,
        f"Exportiert: {exported_at}", f"Exportiert von: {g.user['username']}", "",
        "SYSTEMINFORMATION", "-" * 72,
    ]
    for key, value in info.items():
        lines.append(f"{key}: {', '.join(value) if isinstance(value, list) else value}")
    lines.extend(["", "FILTER", "-" * 72])
    lines.extend((f"{key}: {value}" for key, value in active_filters.items()) if active_filters else ["keine"])
    lines.extend(["", f"EREIGNISSE ({len(rows)})", "=" * 72])
    for row in rows:
        detail = _event_detail(row)
        lines.extend([
            f"[{row['occurred_at']}] {row['outcome']} | {row['actor_name'] or 'System'} | {row['action']}",
            f"Ziel: {row['target_type']} / {row['target_id']}",
            f"Request-ID: {detail.get('request_id', '-')}; App-ID: {detail.get('application_id', '-')}; Client-IP: {detail.get('client_ip', '-')}",
            f"Server: {detail.get('server_name', '-')}; HTTP: {detail.get('method', '-')} {detail.get('endpoint', '-')} -> {detail.get('status', '-')}",
            f"Details: {row['detail'] or '{}'}", "-" * 72,
        ])
    response = Response("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{_export_filename("txt")}"'
    return response


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
