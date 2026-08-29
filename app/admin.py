"""Administrator-only account controls and safe operational diagnostics."""

from __future__ import annotations

import csv
import io
import json
import subprocess
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import FEATURES, activity_for, audit, is_admin, permissions_for, safe_delta, utc_now
from .auth import login_required
from .contact_owner_admin import assign_ownerless_contacts, ownerless_contacts
from .db import get_db
from .osm_address import GEOFABRIK_REGIONS, LocalAddressIndex
from .request_audit import audit_mutation_response
from .runtime_inventory import clear_runtime_inventory, runtime_inventory
from .system_identity import system_info
from tools.launcher import start_osm_download_worker, start_osm_index_worker

bp = Blueprint("admin", __name__, url_prefix="/admin")
bp.after_app_request(audit_mutation_response)

AUDIT_FILTER_KEYS = (
    "q", "actor", "action", "target_type", "target_id", "outcome",
    "request_id", "client_ip", "from_at", "to_at",
)

FEATURE_DETAILS = {
    "documents": ("Dokumente und Suche", "Documents and search", "Dokumentablage, Suche, Vorschau und Sicherheitsprüfung.", "Document storage, search, preview and security checks."),
    "calendar": ("Kalender und CalDAV", "Calendar and CalDAV", "Termine, Buchungsseiten sowie Kalender-Synchronisation.", "Events, booking pages and calendar synchronization."),
    "contacts": ("Kontakte und CardDAV", "Contacts and CardDAV", "Kontakte, CRM-Daten und Adressbuch-Synchronisation.", "Contacts, CRM data and address-book synchronization."),
    "mail": ("E-Mail", "Email", "IMAP, SMTP und Sieve einschließlich gespeicherter Kontoeinstellungen.", "IMAP, SMTP and Sieve including stored account settings."),
    "webdav": ("WebDAV", "WebDAV", "Dateizugriff über WebDAV und zugehörige Einstellungen.", "File access through WebDAV and related settings."),
    "sync": ("Synchronisation", "Synchronization", "Replikation und externe Synchronisationsläufe.", "Replication and external synchronization jobs."),
    "projects": ("Projekte und Zeiten", "Projects and time", "Projekte, Aufgabenbezug und Zeiterfassung.", "Projects, task relations and time tracking."),
    "datalogger": ("Datenlogger", "Data logger", "Sensoren, Messwerte und Datenlogger-Konfiguration.", "Sensors, measurements and data-logger configuration."),
}


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
    query = request.args.get("q", "").strip()[:120].casefold()
    status_filter = request.args.get("status", "all").strip().casefold()
    if status_filter not in {"all", "active", "admin", "disabled"}:
        status_filter = "all"
    visible = []
    for row in rows:
        if query and query not in " ".join(
            str(row[key] or "").casefold() for key in ("username", "display_name", "email")
        ):
            continue
        if status_filter == "active" and row["is_disabled"]:
            continue
        if status_filter == "admin" and not (row["is_admin"] and not row["is_disabled"]):
            continue
        if status_filter == "disabled" and not row["is_disabled"]:
            continue
        visible.append(row)
    orphaned = ownerless_contacts(current_app.config["DOCUMENT_ROOT"])
    return render_template(
        "admin/users.html", users=visible, account_users=rows, features=FEATURES,
        feature_details=FEATURE_DETAILS,
        permissions={row["id"]: permissions_for(row["id"]) for row in visible},
        user_stats={
            "total": len(rows),
            "active": sum(not row["is_disabled"] for row in rows),
            "admins": sum(row["is_admin"] and not row["is_disabled"] for row in rows),
            "disabled": sum(bool(row["is_disabled"]) for row in rows),
        },
        user_query=request.args.get("q", "").strip()[:120], user_status=status_filter,
        ownerless_contacts=orphaned[:100], ownerless_count=len(orphaned),
    )


@bp.get("/inventory")
@admin_required
def inventory():
    return render_template("admin/inventory.html", inventory=runtime_inventory())


@bp.post("/inventory/refresh")
@admin_required
def refresh_inventory():
    clear_runtime_inventory()
    audit("runtime_inventory_refreshed", "system", "runtime")
    flash("Systeminventar wurde neu geprüft. / Runtime inventory refreshed.")
    return redirect(url_for("admin.inventory"))


@bp.get("/osm-addresses")
@admin_required
def osm_address_index():
    index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
    return render_template(
        "admin/osm_address_index.html",
        osm_status=index.status(),
        osm_regions=GEOFABRIK_REGIONS,
    )


@bp.get("/osm-addresses/region-info.json")
@admin_required
def osm_region_info():
    region = request.args.get("region", "").strip()
    index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
    try:
        return jsonify({"region": index.region_info(region), "status": index.status()})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/osm-addresses/status.json")
@admin_required
def osm_status():
    return jsonify(LocalAddressIndex(current_app.config["DOCUMENT_ROOT"]).status())


@bp.post("/osm-addresses/build")
@admin_required
def osm_build():
    region = request.form.get("region", "").strip()
    action = request.form.get("action", "download").strip()
    city = " ".join(request.form.get("city", "").split()).strip()
    index = LocalAddressIndex(current_app.config["DOCUMENT_ROOT"])
    try:
        if action not in {"download", "reindex", "reindex_city"}:
            raise ValueError("Unbekannte OSM-Aktion")
        if action in {"reindex", "reindex_city"}:
            if index.downloaded_source() is None:
                raise ValueError("Kein bereits heruntergeladener OSM-Auszug vorhanden")
            if action == "reindex_city" and (not city or len(city) > 120):
                raise ValueError("Für den Ortsindex ist ein gültiger Ortsname erforderlich")
            if action == "reindex_city":
                start_osm_index_worker(current_app.config["DOCUMENT_ROOT"], force=True, city=city)
            else:
                start_osm_index_worker(current_app.config["DOCUMENT_ROOT"], force=True)
        else:
            if region not in GEOFABRIK_REGIONS:
                raise ValueError("Unbekannte Geofabrik-Region")
            start_osm_download_worker(current_app.config["DOCUMENT_ROOT"], region)
        audit("osm_address_index_started", "service", "osm-addresses", detail={"action": action, "region": region, "city": city})
        flash(f"Teilindex für {city} wurde im Hintergrund gestartet." if action == "reindex_city" else "OSM-Download und Indexierung wurden im Hintergrund gestartet." if action == "download" else "Neuaufbau des lokalen OSM-Adressindex wurde im Hintergrund gestartet.")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        current_app.logger.exception("OSM address index build failed")
        audit("osm_address_index_failed", "service", "osm-addresses", outcome="failure", detail={"action": action, "region": region, "error_type": type(exc).__name__})
        flash(f"OSM-Adressindex konnte nicht aufgebaut werden: {exc}")
    return redirect(url_for("admin.osm_address_index"))


@bp.post("/contacts/assign-owner")
@admin_required
def assign_contact_owner():
    username = request.form.get("owner", "").strip()
    target = get_db().execute(
        "SELECT id, username, is_disabled FROM user WHERE username = ?", (username,)
    ).fetchone()
    if target is None or target["is_disabled"]:
        flash("Der gewählte interne Benutzer ist nicht aktiv oder existiert nicht.")
        return redirect(url_for("admin.users"))

    if request.form.get("all_ownerless") == "1":
        contact_ids = [
            contact.get("contact_id", "")
            for contact in ownerless_contacts(current_app.config["DOCUMENT_ROOT"])
        ]
    else:
        contact_ids = request.form.getlist("contact_id")
    try:
        changed = assign_ownerless_contacts(
            current_app.config["DOCUMENT_ROOT"], contact_ids, username, g.user["username"]
        )
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("admin.users"))

    audit(
        "contact_owner_bulk_assigned", "contacts", username,
        detail={"assigned": changed, "owner": username},
    )
    flash(f"{changed} verwaiste Kontakt(e) wurden {username} zugeordnet.")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    db = get_db()
    target = db.execute("SELECT * FROM user WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        abort(404)
    disabled = request.form.get("is_disabled") == "1"
    administrator = request.form.get("is_admin") == "1"
    display_name = " ".join(request.form.get("display_name", "").split()).strip()
    email = request.form.get("email", "").strip()
    if len(display_name) > 200 or len(email) > 320 or any(character in email for character in "\r\n"):
        flash("Anzeigename oder E-Mail-Adresse ist zu lang oder ungültig.")
        return redirect(url_for("admin.users"))
    if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
        flash("Die E-Mail-Adresse ist nicht plausibel.")
        return redirect(url_for("admin.users"))
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
        "admin": bool(target["is_admin"]), "disabled": bool(target["is_disabled"]),
        "display_name": str(target["display_name"] or ""), "email": str(target["email"] or ""),
        **{f"feature:{key}": value for key, value in permissions_for(user_id).items()},
    }
    requested_features = {feature: request.form.get(f"feature_{feature}") == "1" for feature in FEATURES}
    access_changed = administrator != bool(target["is_admin"]) or disabled != bool(target["is_disabled"]) or any(
        requested_features[key] != before[f"feature:{key}"] for key in FEATURES
    )
    db.execute(
        """UPDATE user SET display_name = ?, email = ?, is_admin = ?, is_disabled = ?,
               auth_version = auth_version + ?,
               updated_at = ? WHERE id = ?""",
        (display_name, email, int(administrator), int(disabled), int(access_changed), utc_now(), user_id),
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
    after = {"admin": administrator, "disabled": disabled, "display_name": display_name, "email": email, **{f"feature:{key}": value for key, value in requested_features.items()}}
    audit("user_access_updated", "user", str(user_id), detail={"username": target["username"], "changes": safe_delta(before, after)})
    flash(f"Konto {target['username']} gespeichert; vorhandene Sitzungen wurden beendet." if access_changed else f"Profildaten für {target['username']} gespeichert; Sitzungen bleiben aktiv.")
    return redirect(url_for("admin.users"))


@bp.get("/activity")
@admin_required
def activity():
    target_type = request.args.get("target_type", "").strip()[:120]
    target_id = request.args.get("target_id", "").strip()[:300]
    if not target_type or not target_id:
        abort(400, description="target_type und target_id sind erforderlich")
    return render_template("admin/activity.html", events=activity_for(target_type, target_id), target_type=target_type, target_id=target_id)


@bp.get("/logs")
@admin_required
def logs():
    try:
        page = max(1, int(request.args.get("page", "1"))); event_page = max(1, int(request.args.get("event_page", "1")))
    except ValueError:
        page = event_page = 1
    limit = 50
    errors = get_db().execute("SELECT * FROM application_error ORDER BY occurred_at DESC LIMIT ? OFFSET ?", (limit + 1, (page - 1) * limit)).fetchall()
    event_filters = _audit_filters(); predicate, parameters = _audit_query(event_filters)
    events = get_db().execute(
        f"SELECT * FROM security_event{predicate} ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
        (*parameters, limit + 1, (event_page - 1) * limit),
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
    filters = _audit_filters(); predicate, parameters = _audit_query(filters)
    rows = get_db().execute(f"SELECT * FROM security_event{predicate} ORDER BY occurred_at DESC LIMIT 20000", parameters).fetchall()
    info = system_info(include_request=True); exported_at = utc_now()
    active_filters = {key: value for key, value in filters.items() if value}
    audit("audit_exported", "audit", export_format, detail={"format": export_format, "rows": len(rows), "filters": active_filters, "application_id": info["application_id"], "request_id": info.get("request_id", "")})
    if export_format == "csv":
        output = io.StringIO(newline=""); writer = csv.writer(output)
        writer.writerow(["record_type", "occurred_at", "actor", "action", "target_type", "target_id", "outcome", "request_id", "application_id", "server_name", "client_ip", "user_agent", "method", "endpoint", "status", "detail_json"])
        writer.writerow(["system", exported_at, g.user["username"], "audit_export", "system", info["application_id"], "success", info.get("request_id", ""), info["application_id"], info["server_name"], info.get("client_ip", ""), info.get("user_agent", ""), "GET", "admin.export_logs", "200", json.dumps({"system": info, "filters": active_filters}, ensure_ascii=False, sort_keys=True)])
        for row in rows:
            detail = _event_detail(row)
            writer.writerow(["event", row["occurred_at"], row["actor_name"] or "System", row["action"], row["target_type"], row["target_id"], row["outcome"], detail.get("request_id", ""), detail.get("application_id", ""), detail.get("server_name", ""), detail.get("client_ip", ""), detail.get("user_agent", ""), detail.get("method", ""), detail.get("endpoint", ""), detail.get("status", ""), row["detail"] or "{}"])
        response = Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = f'attachment; filename="{_export_filename("csv")}"'
        return response
    lines = ["SimpleOffice4Me Audit-Export", "=" * 72, f"Exportiert: {exported_at}", f"Exportiert von: {g.user['username']}", "", "SYSTEMINFORMATION", "-" * 72]
    for key, value in info.items(): lines.append(f"{key}: {', '.join(value) if isinstance(value, list) else value}")
    lines.extend(["", "FILTER", "-" * 72]); lines.extend((f"{key}: {value}" for key, value in active_filters.items()) if active_filters else ["keine"])
    lines.extend(["", f"EREIGNISSE ({len(rows)})", "=" * 72])
    for row in rows:
        detail = _event_detail(row)
        lines.extend([f"[{row['occurred_at']}] {row['outcome']} | {row['actor_name'] or 'System'} | {row['action']}", f"Ziel: {row['target_type']} / {row['target_id']}", f"Request-ID: {detail.get('request_id', '-')}; App-ID: {detail.get('application_id', '-')}; Client-IP: {detail.get('client_ip', '-')}", f"Server: {detail.get('server_name', '-')}; HTTP: {detail.get('method', '-')} {detail.get('endpoint', '-')} -> {detail.get('status', '-')}", f"Details: {row['detail'] or '{}'}", "-" * 72])
    response = Response("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{_export_filename("txt")}"'
    return response


@bp.post("/errors/<int:error_id>/resolve")
@admin_required
def resolve_error(error_id: int):
    result = get_db().execute("UPDATE application_error SET resolved_at = ?, resolved_by = ? WHERE id = ? AND resolved_at IS NULL", (utc_now(), g.user["id"], error_id))
    get_db().commit()
    if result.rowcount: audit("application_error_resolved", "application_error", str(error_id))
    return redirect(url_for("admin.logs"))
