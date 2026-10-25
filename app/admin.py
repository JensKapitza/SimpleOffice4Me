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
from .settings_store import (
    DEFAULT_SETTINGS,
    DEFAULT_CLAMAV_TIMEOUT_SECONDS,
    DEFAULT_RSYNC_TIMEOUT_SECONDS,
    MAX_OSM_READ_TIMEOUT_SECONDS,
    MAX_CLAMAV_TIMEOUT_SECONDS,
    MAX_RSYNC_TIMEOUT_SECONDS,
    MAX_PREVIEW_MAX_BYTES,
    MAX_PREVIEW_MAX_PIXELS,
    MAX_PREVIEW_TIMEOUT_SECONDS,
    MAX_VIDEO_PREVIEW_FRAMES,
    MAX_WEBDAV_QUOTA_MIB,
    DEFAULT_WEBDAV_QUOTA_MIB,
    MIN_PREVIEW_MAX_BYTES,
    MIN_PREVIEW_MAX_PIXELS,
    MIN_PREVIEW_TIMEOUT_SECONDS,
    MIN_VIDEO_PREVIEW_FRAMES,
    MIN_OSM_READ_TIMEOUT_SECONDS,
    MIN_CLAMAV_TIMEOUT_SECONDS,
    MIN_RSYNC_TIMEOUT_SECONDS,
    SettingsStore,
)
from .system_identity import system_info
from tools.launcher import start_osm_download_worker, start_osm_index_worker

bp = Blueprint("admin", __name__, url_prefix="/admin")
bp.after_app_request(audit_mutation_response)

AUDIT_FILTER_KEYS = (
    "q", "actor", "action", "target_type", "target_id", "outcome",
    "request_id", "client_ip", "from_at", "to_at",
)
AUDIT_WHERE = """
 WHERE (?='' OR actor_name LIKE ? OR action LIKE ? OR target_type LIKE ? OR target_id LIKE ? OR detail LIKE ?)
   AND (?='' OR actor_name LIKE ?)
   AND (?='' OR action LIKE ?)
   AND (?='' OR target_type = ?)
   AND (?='' OR target_id LIKE ?)
   AND (?='' OR outcome = ?)
   AND (?='' OR detail LIKE ?)
   AND (?='' OR detail LIKE ?)
   AND (?='' OR occurred_at >= ?)
   AND (?='' OR occurred_at < datetime(?, '+1 day'))
"""

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


def _audit_parameters(filters: dict[str, str]) -> list[object]:
    query = filters["q"]
    needle = f"%{query}%" if query else ""
    actor = f"%{filters['actor']}%" if filters["actor"] else ""
    action = f"%{filters['action']}%" if filters["action"] else ""
    target_id = f"%{filters['target_id']}%" if filters["target_id"] else ""
    request_id = f'%"request_id": "%{filters["request_id"]}%"%' if filters["request_id"] else ""
    client_ip = f'%"client_ip": "%{filters["client_ip"]}%"%' if filters["client_ip"] else ""
    return [
        query, needle, needle, needle, needle, needle,
        filters["actor"], actor,
        filters["action"], action,
        filters["target_type"], filters["target_type"],
        filters["target_id"], target_id,
        filters["outcome"], filters["outcome"],
        filters["request_id"], request_id,
        filters["client_ip"], client_ip,
        filters["from_at"], filters["from_at"],
        filters["to_at"], filters["to_at"],
    ]


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
        parent_names={row["id"]: row["username"] for row in rows},
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
    except ValueError:
        return jsonify({"error": "invalid_region"}), 400


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
    event_filters = _audit_filters(); parameters = _audit_parameters(event_filters)
    events = get_db().execute(
        """SELECT * FROM security_event
           WHERE (?='' OR actor_name LIKE ? OR action LIKE ? OR target_type LIKE ? OR target_id LIKE ? OR detail LIKE ?)
             AND (?='' OR actor_name LIKE ?)
             AND (?='' OR action LIKE ?)
             AND (?='' OR target_type = ?)
             AND (?='' OR target_id LIKE ?)
             AND (?='' OR outcome = ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR occurred_at >= ?)
             AND (?='' OR occurred_at < datetime(?, '+1 day'))
           ORDER BY occurred_at DESC LIMIT ? OFFSET ?""",
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
    filters = _audit_filters(); parameters = _audit_parameters(filters)
    rows = get_db().execute(
        """SELECT * FROM security_event
           WHERE (?='' OR actor_name LIKE ? OR action LIKE ? OR target_type LIKE ? OR target_id LIKE ? OR detail LIKE ?)
             AND (?='' OR actor_name LIKE ?)
             AND (?='' OR action LIKE ?)
             AND (?='' OR target_type = ?)
             AND (?='' OR target_id LIKE ?)
             AND (?='' OR outcome = ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR occurred_at >= ?)
             AND (?='' OR occurred_at < datetime(?, '+1 day'))
           ORDER BY occurred_at DESC LIMIT 20000""",
        parameters,
    ).fetchall()
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


def _bounded_media_integer(name: str, minimum: int, maximum: int) -> int:
    raw = request.form.get(name, "")
    if len(raw) > len(str(maximum)) or not raw.isascii() or not raw.isdigit():
        raise ValueError("Bitte einen gültigen ganzzahligen Medienwert eingeben.")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError("Ein Medienwert liegt außerhalb des zulässigen Bereichs.")
    return value


@bp.get("/settings/documents-media")
@admin_required
def documents_media_settings():
    media = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["media"]
    return render_template(
        "admin/documents_media_settings.html",
        media=media,
    )


@bp.get("/settings/network")
@admin_required
def network_settings():
    settings = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()
    return render_template(
        "admin/network_settings.html",
        network=settings["network"],
    )


@bp.get("/settings/integrations")
@admin_required
def integration_settings():
    integrations = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()[
        "integrations"
    ]
    return render_template(
        "admin/integration_settings.html",
        integrations=integrations,
    )


@bp.post("/settings/network")
@admin_required
def save_network_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["network"]
        if reset_defaults:
            network = {
                "webdav_quota_mib": DEFAULT_SETTINGS["network"]["webdav_quota_mib"],
                "rsync_timeout_seconds": (
                    DEFAULT_SETTINGS["network"]["rsync_timeout_seconds"]
                    if "rsync_timeout_seconds" in request.form
                    else current["rsync_timeout_seconds"]
                ),
            }
        else:
            raw = request.form.get("webdav_quota_mib")
            if raw is None:
                quota = current["webdav_quota_mib"]
            else:
                if not raw.isascii() or not raw.isdigit() or len(raw) > len(str(MAX_WEBDAV_QUOTA_MIB)):
                    raise ValueError("Bitte ein gültiges WebDAV-Kontingent in MiB eingeben.")
                quota = int(raw)
            if not DEFAULT_WEBDAV_QUOTA_MIB <= quota <= MAX_WEBDAV_QUOTA_MIB:
                raise ValueError("Bitte ein gültiges WebDAV-Kontingent in MiB eingeben.")
            raw_timeout = request.form.get("rsync_timeout_seconds")
            if raw_timeout is None:
                rsync_timeout = current["rsync_timeout_seconds"]
            else:
                if (
                    not raw_timeout.isascii()
                    or not raw_timeout.isdigit()
                    or len(raw_timeout) > len(str(MAX_RSYNC_TIMEOUT_SECONDS))
                ):
                    raise ValueError("Bitte ein ganzzahliges Rsync-Zeitlimit eingeben.")
                rsync_timeout = int(raw_timeout)
                if not MIN_RSYNC_TIMEOUT_SECONDS <= rsync_timeout <= MAX_RSYNC_TIMEOUT_SECONDS:
                    raise ValueError("Das Rsync-Zeitlimit muss zwischen 30 und 86400 Sekunden liegen.")
            network = {"webdav_quota_mib": quota, "rsync_timeout_seconds": rsync_timeout}
        webdav_changed = network["webdav_quota_mib"] != current["webdav_quota_mib"]
        rsync_changed = network["rsync_timeout_seconds"] != current["rsync_timeout_seconds"]
        store.save(
            {"network": network}, str(g.user["username"]),
        )
        if webdav_changed or not rsync_changed:
            audit(
                "network_settings_updated", "settings", "network.webdav_quota",
                detail={
                    "webdav_quota_mib": network["webdav_quota_mib"],
                    "reset_to_defaults": reset_defaults,
                    "restart_required": webdav_changed,
                },
            )
        if rsync_changed:
            audit(
                "rsync_timeout_updated", "settings", "network.rsync_timeout_seconds",
                detail={"rsync_timeout_seconds": network["rsync_timeout_seconds"],
                        "reset_to_defaults": reset_defaults, "restart_required": False},
            )
        notice = (
            "Netzwerk-Einstellungen auf Standard zurückgesetzt."
            if reset_defaults else "Netzwerk-Einstellungen gespeichert."
        )
        if webdav_changed:
            notice += " Neustart erforderlich, damit das WebDAV-Kontingent gilt."
        if rsync_changed:
            notice += " Das Rsync-Zeitlimit gilt für neue Sitzungen."
        flash(notice)
    except ValueError as exc:
        current_app.logger.info("Network setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Network settings save failed: %s", type(exc).__name__)
        flash("Netzwerk-Einstellungen konnten nicht gespeichert werden.")
    return redirect(url_for("admin.network_settings"))


@bp.post("/settings/integrations/osm")
@admin_required
def save_osm_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        raw = str(DEFAULT_SETTINGS["integrations"]["osm_download_read_timeout_seconds"])
        if not reset_defaults:
            raw = request.form.get("osm_download_read_timeout_seconds", "")
        if (
            not raw.isascii()
            or not raw.isdigit()
            or len(raw) > len(str(MAX_OSM_READ_TIMEOUT_SECONDS))
        ):
            raise ValueError("Bitte einen ganzzahligen OSM-Lese-Timeout eingeben.")
        timeout = int(raw)
        if not MIN_OSM_READ_TIMEOUT_SECONDS <= timeout <= MAX_OSM_READ_TIMEOUT_SECONDS:
            raise ValueError("Der OSM-Lese-Timeout muss zwischen 30 und 3600 Sekunden liegen.")
        integrations = {"osm_download_read_timeout_seconds": timeout}
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"integrations": integrations}, str(g.user["username"]),
        )
        audit(
            "osm_download_settings_updated",
            "settings",
            "integrations.osm_download_read_timeout_seconds",
            detail={**integrations, "reset_to_defaults": reset_defaults},
        )
        flash(
            "OSM-Download-Einstellungen auf Standard zurückgesetzt."
            if reset_defaults else "OSM-Download-Einstellungen gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("OSM setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("OSM settings save failed: %s", type(exc).__name__)
        flash("OSM-Download-Einstellungen konnten nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))


@bp.post("/settings/integrations/clamav")
@admin_required
def save_clamav_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        raw = str(DEFAULT_CLAMAV_TIMEOUT_SECONDS)
        if not reset_defaults:
            raw = request.form.get("clamav_timeout_seconds", "")
        if (
            not raw.isascii()
            or not raw.isdigit()
            or len(raw) > len(str(MAX_CLAMAV_TIMEOUT_SECONDS))
        ):
            raise ValueError("Bitte ein ganzzahliges ClamAV-Zeitlimit eingeben.")
        timeout = int(raw)
        if not MIN_CLAMAV_TIMEOUT_SECONDS <= timeout <= MAX_CLAMAV_TIMEOUT_SECONDS:
            raise ValueError("Das ClamAV-Zeitlimit muss zwischen 5 und 900 Sekunden liegen.")
        integrations = {"clamav_timeout_seconds": timeout}
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"integrations": integrations}, str(g.user["username"]),
        )
        audit(
            "clamav_settings_updated",
            "settings",
            "integrations.clamav_timeout_seconds",
            detail={**integrations, "reset_to_defaults": reset_defaults},
        )
        flash(
            "ClamAV-Einstellungen auf Standard zurückgesetzt."
            if reset_defaults else "ClamAV-Einstellungen gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("ClamAV setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("ClamAV settings save failed: %s", type(exc).__name__)
        flash("ClamAV-Einstellungen konnten nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))


@bp.post("/settings/documents-media")
@admin_required
def save_documents_media_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        if reset_defaults:
            media = dict(DEFAULT_SETTINGS["media"])
        else:
            enabled = request.form.get("preview_enabled", "")
            if enabled not in {"0", "1"}:
                raise ValueError("Bitte wählen, ob Dokumentvorschauen aktiviert sind.")
            media = {
                "preview_enabled": enabled == "1",
                "preview_timeout_seconds": _bounded_media_integer(
                    "preview_timeout_seconds", MIN_PREVIEW_TIMEOUT_SECONDS, MAX_PREVIEW_TIMEOUT_SECONDS,
                ),
                "preview_max_bytes": _bounded_media_integer(
                    "preview_max_bytes", MIN_PREVIEW_MAX_BYTES, MAX_PREVIEW_MAX_BYTES,
                ),
                "preview_max_pixels": _bounded_media_integer(
                    "preview_max_pixels", MIN_PREVIEW_MAX_PIXELS, MAX_PREVIEW_MAX_PIXELS,
                ),
                "video_preview_frames": _bounded_media_integer(
                    "video_preview_frames", MIN_VIDEO_PREVIEW_FRAMES, MAX_VIDEO_PREVIEW_FRAMES,
                ),
            }
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"media": media}, str(g.user["username"]),
        )
        audit(
            "documents_media_settings_updated",
            "settings",
            "media.preview_settings",
            detail={**media, "reset_to_defaults": reset_defaults},
        )
        flash(
            "Dokument- und Medienwerte auf Standard zurückgesetzt."
            if reset_defaults else "Dokument- und Medien-Einstellungen gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("Document/media setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Document and media settings save failed: %s", type(exc).__name__)
        flash("Dokument- und Medien-Einstellungen konnten nicht gespeichert werden.")
    return redirect(url_for("admin.documents_media_settings"))
