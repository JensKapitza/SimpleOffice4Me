"""Authenticated web pages for document versions, notes and audit history."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from calendar import month_name, monthcalendar
from datetime import date, datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .document_store import DocumentStore
from .contact_store import ContactStore
from .calendar_store import CalendarStore
from .calendar_collections import CalendarCollections
from .google_calendar_sync import GoogleCalendarError, GoogleCalendarSync
from .caldav_scheduling import SchedulingAccess, local_calendar_address
from .itip import ItipConflict, ItipStore, MAX_MESSAGE_BYTES
from .ics_preview import MAX_PREVIEW_BYTES, preview_ics
from .todo_store import TodoStore
from .settings_store import SettingsStore
from .form_store import FormStore
from .project_store import ProjectStore
from .replication_store import CATEGORIES, ReplicationStore
from .object_store import ObjectStore
from .attachment_security import AttachmentSecurity, ClamAV
from .db import get_db


bp = Blueprint("documents", __name__, url_prefix="/documents")


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _contacts() -> ContactStore:
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _calendar() -> CalendarStore:
    return CalendarStore(current_app.config["DOCUMENT_ROOT"])


def _itip() -> ItipStore:
    return ItipStore(current_app.config["DOCUMENT_ROOT"])


def _calendars() -> CalendarCollections:
    return CalendarCollections(current_app.config["DOCUMENT_ROOT"])


def _google_calendar() -> GoogleCalendarSync:
    return GoogleCalendarSync(current_app.config["DOCUMENT_ROOT"])


def _scheduling_access() -> SchedulingAccess:
    return SchedulingAccess(current_app.config["DOCUMENT_ROOT"])


def _todos() -> TodoStore:
    return TodoStore(current_app.config["DOCUMENT_ROOT"])


def _settings() -> SettingsStore:
    return SettingsStore(current_app.config["DOCUMENT_ROOT"])


def _forms() -> FormStore:
    return FormStore(current_app.config["DOCUMENT_ROOT"])


def _projects() -> ProjectStore:
    return ProjectStore(current_app.config["DOCUMENT_ROOT"])


def _replication() -> ReplicationStore:
    return ReplicationStore(current_app.config["DOCUMENT_ROOT"])


def _objects() -> ObjectStore:
    return ObjectStore(current_app.config["DOCUMENT_ROOT"])


def _attachment_security() -> AttachmentSecurity:
    return AttachmentSecurity(current_app.config["DOCUMENT_ROOT"])


def _security_admin(actor: str) -> bool:
    configured = {item.strip() for item in os.environ.get("SIMPLEOFFICE_SECURITY_ADMINS", "").split(",") if item.strip()}
    return actor in configured


def _form_relation_choices(form: dict, actor: str) -> dict[str, list[tuple[str, str]]]:
    """Resolve form relations, including the canonical contact master data."""
    choices: dict[str, list[tuple[str, str]]] = {}
    for field in form.get("fields", []):
        if field.get("type") != "relation":
            continue
        target_id = field.get("relation_form", "")
        if target_id == "contact":
            choices[field["key"]] = [
                (item["contact_id"], item.get("fields", {}).get("display_name", item["contact_id"]))
                for item in _contacts().contacts(actor)
            ]
            continue
        try:
            target = _forms().definition(target_id)
            choices[field["key"]] = [
                (item["record_id"], item.get("values", {}).get(target["title_field"], item["record_id"]))
                for item in _forms().records(target_id)
            ]
        except ValueError:
            choices[field["key"]] = []
    return choices


def _invoice_products() -> list[dict[str, str]]:
    """Small product projection used by the invoice-position dropdown."""
    try:
        product_form = _forms().definition("product")
    except ValueError:
        return []
    return [
        {
            "id": item["record_id"],
            "name": item.get("values", {}).get(product_form["title_field"], item["record_id"]),
            "description": item.get("values", {}).get("description", ""),
            "unit_price": item.get("values", {}).get("sales_price", ""),
            "tax_rate": item.get("values", {}).get("tax_rate", "19"),
        }
        for item in _forms().records("product")
    ]


def _system_overview() -> dict:
    root = _store().root
    storage = shutil.disk_usage(root)
    # Enumerating every OS mount can block login on unavailable SMB/NFS media.
    # External archives remain available through their explicit discovery UI.
    return {"time": datetime.now().astimezone(), "root": str(root), "storage": storage}


def _calendar_tags() -> list[dict[str, str]]:
    return [
        {"name": tag.strip(), "visibility": visibility}
        for visibility in ("private", "family", "external")
        for tag in request.form.get(f"{visibility}_tags", "").split(",")
        if tag.strip()
    ]


def _calendar_metadata() -> dict[str, Any]:
    conferences = []
    for line in request.form.get("conferences", "").splitlines():
        if not line.strip():
            continue
        uri, label, features = (line.split("|", 2) + ["", ""])[:3]
        conferences.append({
            "uri": uri.strip(),
            "label": label.strip(),
            "features": [item.strip() for item in features.split(",") if item.strip()],
        })
    return {
        "ical_status": request.form.get("ical_status", "confirmed"),
        "transparency": request.form.get("transparency", "opaque"),
        "classification": request.form.get("classification", "private"),
        "priority": request.form.get("priority", "0"),
        "location": request.form.get("location", ""),
        "event_url": request.form.get("event_url", ""),
        "resources": [item.strip() for item in request.form.get("resources", "").split(",") if item.strip()],
        "conferences": conferences,
    }


def _document_or_404(document_id: str) -> dict:
    try:
        return _store().get_document(document_id)
    except ValueError:
        abort(404)


def _preview_data(document: dict) -> dict[str, str]:
    suffix = Path(str(document.get("last_path", ""))).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}: kind, icon = "image", "fa-file-image"
    elif suffix == ".pdf": kind, icon = "pdf", "fa-file-pdf"
    elif suffix in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}: kind, icon = "audio", "fa-file-audio"
    elif suffix in {".mp4", ".mkv", ".mov", ".avi", ".webm"}: kind, icon = "video", "fa-file-video"
    elif document.get("extracted_text") or document.get("ocr_text"): kind, icon = "text", "fa-file-lines"
    elif suffix in {".doc", ".docx", ".odt", ".rtf"}: kind, icon = "document", "fa-file-word"
    elif suffix in {".xls", ".xlsx", ".ods", ".csv"}: kind, icon = "document", "fa-file-excel"
    else: kind, icon = "file", "fa-file"
    return {"kind": kind, "icon": icon, "mime": mimetypes.guess_type(str(document.get("last_path", "")))[0] or "application/octet-stream"}


def _is_unprocessed(document: dict) -> bool:
    """Inbox contains only new files without a human note, state or relation."""
    return document.get("state", "new") == "new" and not document.get("notes") and not document.get("relationships")


def _document_tree(documents: list[dict]) -> dict:
    """Build a folder tree for a compact, progressively disclosed document view."""
    root = {"folders": {}, "documents": [], "count": 0}
    for document in documents:
        current = root; current["count"] += 1
        parts = [part for part in str(document.get("last_path", "")).split("/") if part]
        for folder in parts[:-1]:
            current = current["folders"].setdefault(folder, {"folders": {}, "documents": [], "count": 0})
            current["count"] += 1
        current["documents"].append(document)
    return root


@bp.route("/")
@login_required
def index():
    try: page = max(1, int(request.args.get("page", "1")))
    except ValueError: page = 1
    result = _store().document_page(page=page)
    documents = result["documents"]
    return render_template(
        "documents/index.html",
        document_tree=_document_tree(documents),
        defaults=_settings().settings(),
        **result,
    )


@bp.get("/search")
@login_required
def document_search():
    query = request.args.get("q", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    result = {"results": [], "page": page, "page_size": 25, "has_next": False}
    if query:
        # Never scan or backfill here: an initial scan may take a long time and
        # runs independently in the launcher.  The existing index can answer
        # immediately and is extended as the background scan progresses.
        result = _store().search_page(query, page=page)
        for item in result["results"]:
            _store().record_access(item["document_id"], str(g.user["username"]), "found")
    return render_template("documents/search.html", query=query, scan_status=_store().scan_status(), **result)


@bp.post("/search/index")
@login_required
def refresh_document_search():
    try:
        updated = _store().refresh_missing_text(str(g.user["username"]))
        flash(f"Textextraktion aktualisiert: {updated} Dokument(e) ergänzt.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(f"Textextraktion fehlgeschlagen: {exc}")
    return redirect(url_for("documents.document_search", q=request.form.get("q", "").strip()))


@bp.get("/dashboard")
@login_required
def dashboard():
    inbox = _store().inbox_page(page=1, page_size=8)
    return render_template(
        "documents/dashboard.html",
        system=_system_overview(),
        inbox=inbox["documents"],
        inbox_total=inbox["total"],
        todos=_todos().items(),
        pending=_calendar().pending_bookings(),
        scan_status=_store().scan_status(),
    )


@bp.route("/objects", methods=("GET", "POST"))
@login_required
def objects():
    if request.method == "POST":
        try:
            item = _objects().create(request.form.to_dict(), str(g.user["username"]))
            flash("Objekt wurde angelegt.")
            return redirect(url_for("documents.object_detail", object_id=item["object_id"]))
        except ValueError as exc:
            flash(str(exc))
    query = request.args.get("q", "").strip()
    return render_template("documents/objects.html", objects=_objects().objects(query), query=query)


@bp.route("/objects/<object_id>", methods=("GET", "POST"))
@login_required
def object_detail(object_id: str):
    try:
        if request.method == "POST":
            _objects().update(object_id, request.form.to_dict(), str(g.user["username"]))
            flash("Objekt wurde gespeichert.")
            return redirect(url_for("documents.object_detail", object_id=object_id))
        item = _objects().object(object_id)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("documents.objects"))
    attached = []
    for document_id in item.get("document_ids", []):
        try:
            attached.append(_store().get_document(document_id))
        except ValueError:
            attached.append({"document_id": document_id, "last_path": "[Dokument fehlt]"})
    document_query = request.args.get("document_query", "").strip()
    matches = _store().search(document_query, limit=20) if document_query else []
    return render_template(
        "documents/object_detail.html",
        item=item,
        attached=attached,
        matches=[match for match in matches if match["document_id"] not in item.get("document_ids", [])],
        document_query=document_query,
    )


@bp.post("/objects/<object_id>/documents")
@login_required
def attach_object_document(object_id: str):
    try:
        document_id = request.form.get("document_id", "")
        _store().get_document(document_id)
        _objects().attach_document(object_id, document_id, str(g.user["username"]))
        flash("Dokument wurde mit dem Objekt verbunden.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.object_detail", object_id=object_id))


@bp.post("/objects/<object_id>/documents/<document_id>/remove")
@login_required
def detach_object_document(object_id: str, document_id: str):
    try:
        _objects().detach_document(object_id, document_id, str(g.user["username"]))
        flash("Dokumentverknüpfung wurde entfernt; die Datei blieb unverändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.object_detail", object_id=object_id))


@bp.post("/objects/<object_id>/notes")
@login_required
def add_object_note(object_id: str):
    try:
        _objects().add_note(object_id, request.form.get("text", ""), str(g.user["username"]))
        flash("Notiz wurde gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.object_detail", object_id=object_id))


@bp.route("/projects", methods=("GET", "POST"))
@login_required
def projects():
    if request.method == "POST":
        try:
            project = _projects().create_project(request.form.to_dict(), str(g.user["username"]))
            flash("Projekt angelegt.")
            return redirect(url_for("documents.project_detail", project_id=project["project_id"]))
        except ValueError as exc:
            flash(str(exc))
    return render_template("documents/projects.html", projects=_projects().projects())


@bp.route("/projects/<project_id>", methods=("GET", "POST"))
@login_required
def project_detail(project_id: str):
    try:
        if request.method == "POST":
            _projects().update_project(project_id, request.form.to_dict(), str(g.user["username"]))
            flash("Projekt gespeichert.")
            return redirect(url_for("documents.project_detail", project_id=project_id))
        project = _projects().project(project_id)
    except ValueError as exc:
        flash(str(exc)); return redirect(url_for("documents.projects"))
    linked_documents = []
    for document_id in project.get("document_ids", []):
        try:
            linked_documents.append(_store().get_document(document_id))
        except ValueError:
            continue
    return render_template("documents/project_detail.html", project=project, linked_documents=linked_documents)


@bp.post("/projects/<project_id>/tasks")
@login_required
def add_project_task(project_id: str):
    try:
        values = request.form.to_dict(); values["predecessors"] = request.form.getlist("predecessors")
        _projects().add_task(project_id, values, str(g.user["username"]))
        flash("Aufgabe angelegt.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + "#aufgaben")


@bp.post("/projects/<project_id>/tasks/<task_id>")
@login_required
def update_project_task(project_id: str, task_id: str):
    try:
        values = request.form.to_dict(); values["predecessors"] = request.form.getlist("predecessors")
        _projects().update_task(project_id, task_id, values, str(g.user["username"]))
        flash("Aufgabe gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + f"#task-{task_id}")


@bp.post("/projects/<project_id>/tasks/<task_id>/time")
@login_required
def book_project_task_time(project_id: str, task_id: str):
    try:
        entry = _projects().book_time(project_id, task_id, request.form.get("date", ""), request.form.get("hours", ""), request.form.get("note", ""), str(g.user["username"]))
        flash(f"{entry['minutes'] / 60:g} Stunden gebucht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + f"#task-{task_id}")


@bp.post("/projects/<project_id>/notes")
@login_required
def add_project_note(project_id: str):
    try: _projects().add_note(project_id, request.form.get("text", ""), str(g.user["username"]), request.form.get("task_id", "")); flash("Notiz gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + "#akte")


@bp.post("/projects/<project_id>/links")
@login_required
def add_project_link(project_id: str):
    try: _projects().add_link(project_id, request.form.get("url", ""), request.form.get("label", ""), str(g.user["username"]), request.form.get("task_id", "")); flash("Link gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + "#akte")


@bp.post("/projects/<project_id>/documents")
@login_required
def attach_project_document(project_id: str):
    document_id = request.form.get("document_id", "")
    try:
        _document_or_404(document_id)
        _projects().attach_document(project_id, document_id, str(g.user["username"]), request.form.get("task_id", "")); flash("Datei verknüpft.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + "#akte")


@bp.get("/projects/documents/search")
@login_required
def search_project_documents():
    query = request.args.get("q", "").strip()
    results = _store().search_page(query, page_size=20)["results"] if len(query) >= 2 else []
    for result in results:
        _store().record_access(result["document_id"], str(g.user["username"]), "found")
    return Response(json.dumps(results), mimetype="application/json")


@bp.get("/replication")
@login_required
def replication():
    return render_template("documents/replication.html", status=_replication().status(), categories=CATEGORIES, restic_installed=shutil.which("restic") is not None)


@bp.post("/replication/targets")
@login_required
def add_replication_target():
    try:
        target = _replication().add_target(request.form.to_dict(), str(g.user["username"])); result = target.get("initial_import", {})
        flash(f"Speicherziel angelegt. Import: {result.get('copied', 0)} neu, {result.get('unchanged', 0)} vorhanden, {result.get('errors', 0)} nicht lesbar.")
    except (OSError, ValueError) as exc: flash(str(exc))
    return redirect(url_for("documents.replication"))


@bp.post("/replication/targets/<target_id>/import")
@login_required
def import_replication_target(target_id: str):
    try:
        result = _replication().import_target(target_id, str(g.user["username"]))
        flash(f"Speicher importiert: {result['copied']} neu, {result['unchanged']} bereits vorhanden, {result.get('errors', 0)} nicht lesbar.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.replication"))


@bp.post("/replication/targets/<target_id>/enabled")
@login_required
def set_replication_target_enabled(target_id: str):
    try:
        enabled = request.form.get("enabled") == "1"
        _replication().set_target_enabled(target_id, enabled, str(g.user["username"]))
        flash("Speicherziel aktiviert." if enabled else "Speicherziel pausiert. Import und Spiegelung sind angehalten.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.replication"))


@bp.post("/replication/rules")
@login_required
def add_replication_rule():
    try:
        values = request.form.to_dict(); values["categories"] = request.form.getlist("categories")
        _replication().add_rule(values, str(g.user["username"])); flash("Spiegelungsregel angelegt.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.replication"))


@bp.post("/replication/rules/<rule_id>/run")
@login_required
def run_replication_rule(rule_id: str):
    try:
        result = _replication().run_rule(rule_id, str(g.user["username"])); flash(f"Spiegelung abgeschlossen: {result['copied']} kopiert, {result['unchanged']} unverändert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.replication"))


@bp.post("/replication/restic")
@login_required
def add_restic_repository():
    try:
        values = request.form.to_dict(); values["categories"] = request.form.getlist("categories")
        _replication().add_restic_repository(values, str(g.user["username"])); flash("Restic-Repository angelegt. Das Passwort wurde nicht gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.replication"))


@bp.post("/replication/restic/<repository_id>/<action>")
@login_required
def run_restic(repository_id: str, action: str):
    try:
        result = _replication().run_restic(repository_id, action, request.form.get("password", ""), str(g.user["username"]), request.form.get("restore_path", "")); flash(result["output"] or f"Restic {action} abgeschlossen.")
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc: flash(str(exc))
    return redirect(url_for("documents.replication"))


@bp.route("/settings")
@login_required
def settings():
    return render_template("documents/settings.html", settings=_settings().settings())


@bp.post("/settings")
@login_required
def save_settings():
    values = {
        "interface": {"default_language": request.form.get("default_language", "de"), "timezone": request.form.get("timezone", "Europe/Berlin")},
        "documents": {"default_state": request.form.get("default_state", "new"), "default_tags": request.form.get("default_tags", "").split(","), "upload_to_archive": request.form.get("upload_to_archive") == "1"},
        "calendar": {"default_visibility": request.form.get("default_visibility", "private"), "default_public_notice": request.form.get("default_public_notice", "Belegt"), "default_duration_minutes": request.form.get("default_duration_minutes", "60")},
        "sharing": {"default_expiry_days": request.form.get("default_expiry_days", "7")},
    }
    try:
        _settings().save(values, str(g.user["username"]))
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            raise ValueError("Anzeigename fehlt.")
        get_db().execute("UPDATE user SET display_name = ?, profile_source = ?, profile_updated_at = CURRENT_TIMESTAMP WHERE id = ?", (display_name, "manual", g.user["id"]))
        get_db().commit()
        flash("Standardwerte gespeichert. Bestehende Daten wurden nicht verändert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.settings"))


@bp.post("/settings/language")
@login_required
def set_language():
    language = request.form.get("language", "de")
    if language in {"de", "en"}:
        from flask import session
        session["simpleoffice_language"] = language
    return redirect(request.referrer or url_for("documents.settings"))


@bp.post("/todo")
@login_required
def add_todo():
    try: _todos().add(request.form.get("title", ""), str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.dashboard"))


@bp.post("/todo/<item_id>/toggle")
@login_required
def toggle_todo(item_id: str):
    try: _todos().toggle(item_id, str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.dashboard"))


@bp.route("/inbox")
@login_required
def inbox():
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    result = _store().inbox_page(page=page)
    documents = result["documents"]
    return render_template(
        "documents/index.html",
        document_tree=_document_tree(documents),
        inbox_only=True,
        defaults=_settings().settings(),
        **result,
    )


@bp.route("/images")
@login_required
def images():
    tag = request.args.get("tag", "").strip()
    period = request.args.get("period", "all")
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    page_size = 500
    if period not in {"all", "week", "month", "year"}:
        period = "all"
    now = datetime.now(timezone.utc)

    def in_period(item: dict) -> bool:
        if period == "all":
            return True
        try:
            seen = datetime.fromisoformat(item.get("first_seen_at", "").replace("Z", "+00:00"))
        except ValueError:
            return False
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if period == "week":
            return seen >= now - timedelta(days=7)
        if period == "month":
            return seen.year == now.year and seen.month == now.month
        return seen.year == now.year

    matches = [
        item for item in _store().list_documents()
        if item.get("last_path", "").lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
        and (not tag or any(_store().tag_matches(tag, item_tag) for item_tag in item.get("tags", []))) and in_period(item)
    ]
    total = len(matches)
    start = (page - 1) * page_size
    pictures = matches[start:start + page_size]
    return render_template(
        "documents/images.html",
        pictures=pictures,
        tag=tag,
        period=period,
        page=page,
        page_size=page_size,
        total=total,
        has_next=start + len(pictures) < total,
    )


@bp.get("/<document_id>/preview")
@login_required
def image_preview(document_id: str):
    document = _document_or_404(document_id); path = _store().root / document.get("last_path", "")
    if not path.is_file() or path.is_symlink(): abort(404)
    return send_file(path)


@bp.post("/<document_id>/tags")
@login_required
def set_document_tags(document_id: str):
    try: _store().set_tags(document_id, request.form.get("tags", "").split(","), str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(request.referrer or url_for("documents.images"))


@bp.post("/<document_id>/portable-metadata")
@login_required
def export_portable_metadata(document_id: str):
    try:
        sidecar = _store().export_portable_metadata(document_id, str(g.user["username"]))
        flash(f"Portable Metadaten aktualisiert: {sidecar.name}")
    except (OSError, ValueError) as exc:
        flash(f"Metadatenexport fehlgeschlagen: {exc}")
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/portable-metadata/export-all")
@login_required
def export_all_portable_metadata():
    result = _store().export_all_portable_metadata(str(g.user["username"]))
    flash(f"Portable Metadaten: {result['exported']} exportiert, {result['errors']} Fehler.")
    return redirect(url_for("documents.index"))


@bp.post("/<document_id>/analyze-image")
@login_required
def analyze_image(document_id: str):
    try:
        _store().analyze_image(document_id, str(g.user["username"]))
        flash("Bild analysiert: EXIF, OCR und Tags wurden aktualisiert.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(f"Bildanalyse fehlgeschlagen: {exc}")
    return redirect(request.referrer or url_for("documents.images"))


@bp.post("/upload")
@login_required
def upload():
    files = [item for item in request.files.getlist("files") if item and item.filename]
    if not files:
        flash("Bitte mindestens eine Datei auswählen.")
        return redirect(url_for("documents.index"))
    stored = 0
    defaults = _settings().settings()["documents"]
    for item in files:
        try:
            metadata = _store().import_upload(
                item,
                item.filename,
                str(g.user["username"]),
                request.form.get("archive") == "1",
                max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
            )
            if defaults["default_tags"]:
                _store().set_tags(metadata["document_id"], [*metadata.get("tags", []), *defaults["default_tags"]], str(g.user["username"]))
            if defaults["default_state"] != "new":
                _store().set_state(metadata["document_id"], defaults["default_state"], str(g.user["username"]))
            stored += 1
        except (OSError, ValueError) as exc:
            flash(f"{item.filename}: {exc}")
    if stored:
        flash(f"{stored} Datei(en) vollständig und hashbasiert importiert.")
    return redirect(url_for("documents.index"))


@bp.route("/<document_id>")
@login_required
def detail(document_id: str):
    document = _document_or_404(document_id)
    store = _store()
    document = store.record_access(document_id, str(g.user["username"]), "seen")
    query = request.args.get("link_query", "").strip()
    linked_documents = store.relationship_targets(document)
    relationships = [{**relationship, "target": linked_documents.get(relationship.get("target_document_id"))} for relationship in document.get("relationships", [])]
    return render_template(
        "documents/detail.html",
        document=document,
        versions=store.versions(document_id),
        content_recovery_versions=store.content_recovery_versions(document_id),
        relationships=relationships,
        shares=store.document_shares(document_id),
        retention=store.retention_status(document_id),
        link_query=query,
        link_matches=[item for item in store.search(query, limit=10) if item["document_id"] != document_id] if query else [],
        preview={**_preview_data(document), "url": url_for("documents.image_preview", document_id=document_id), "name": document.get("last_path", "").rsplit("/", 1)[-1], "text": (document.get("extracted_text") or document.get("ocr_text") or "")[:12000]},
        defaults=_settings().settings(),
    )


@bp.get("/recovery")
@login_required
def document_recovery():
    actor = str(g.user["username"])
    return render_template("documents/recovery.html", items=_store().recovery_items(actor))


@bp.post("/recovery/<document_id>/restore")
@login_required
def restore_deleted_document(document_id: str):
    if request.form.get("confirm") != "WIEDERHERSTELLEN":
        flash("Zur Wiederherstellung muss WIEDERHERSTELLEN bestätigt werden.")
        return redirect(url_for("documents.document_recovery"))
    try:
        restored = _store().restore_soft_deleted(
            document_id,
            request.form.get("destination_path", ""),
            request.form.get("expected_sha256", ""),
            str(g.user["username"]),
        )
        from .webdav import _record_sync_changes
        _record_sync_changes(str(g.user["username"]), str(restored["last_path"]))
        flash(f"Dokument wurde ohne Überschreiben nach {restored['last_path']} wiederhergestellt.")
        return redirect(url_for("documents.detail", document_id=document_id))
    except PermissionError:
        abort(404)
    except (FileExistsError, OSError, ValueError) as exc:
        flash(f"Wiederherstellung nicht ausgeführt: {exc}")
        return redirect(url_for("documents.document_recovery"))


@bp.post("/<document_id>/restore-content")
@login_required
def restore_document_content(document_id: str):
    _document_or_404(document_id)
    if request.form.get("confirm") != "WIEDERHERSTELLEN":
        flash("Zur Wiederherstellung muss WIEDERHERSTELLEN bestätigt werden.")
        return redirect(url_for("documents.detail", document_id=document_id))
    try:
        restored = _store().restore_content_version(
            document_id,
            request.form.get("archived_sha256", ""),
            request.form.get("expected_current_sha256", ""),
            str(g.user["username"]),
            max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
        )
        from .webdav import _record_sync_changes
        _record_sync_changes(str(g.user["username"]), str(restored["last_path"]))
        flash(f"Inhaltsversion als neue Revision {restored['content_revision']} wiederhergestellt.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(f"Inhaltsversion nicht wiederhergestellt: {exc}")
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.route("/<document_id>/attachments", methods=["GET", "POST"])
@login_required
def document_attachments(document_id: str):
    actor = str(g.user["username"])
    document = _document_or_404(document_id)
    if Path(str(document.get("last_path", ""))).suffix.casefold() != ".eml":
        abort(404)
    try:
        if request.method == "GET":
            manifest = _attachment_security().preview_eml(document_id, actor)
            return render_template("documents/attachments.html", document=document, manifest=manifest)
        selected = [int(value) for value in request.form.getlist("parts")]
        results = _attachment_security().extract(request.form.get("manifest_id", ""), selected, actor)
        clean = sum(1 for row in results if row.get("verdict") == "clean")
        infected = sum(1 for row in results if row.get("verdict") == "infected")
        flash(f"{clean} Anhang/Anhänge sicher übernommen; {infected} infizierte Datei(en) bleiben in Quarantäne.")
    except (OSError, PermissionError, RuntimeError, ValueError) as exc:
        flash(f"Anhänge wurden nicht freigegeben: {exc}")
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.get("/security")
@login_required
def security_center():
    actor = str(g.user["username"])
    security = _attachment_security()
    return render_template("documents/security.html", status=security.scanner.status(), scans=security.recent_scans(), is_admin=_security_admin(actor))


@bp.post("/security/scan-now")
@login_required
def security_scan_now():
    actor = str(g.user["username"])
    if not _security_admin(actor): abort(403)
    try: flash(f"Serverprüfung abgeschlossen: {_attachment_security().scan_documents(actor)}")
    except (OSError, RuntimeError, ValueError) as exc: flash(f"Serverprüfung fehlgeschlagen: {exc}")
    return redirect(url_for("documents.security_center"))


@bp.post("/security/update")
@login_required
def security_update():
    actor = str(g.user["username"])
    if not _security_admin(actor): abort(403)
    try:
        output = ClamAV().update()
        _store().history.record("clamav_signatures_updated", actor, "security", "clamav", {"output": output[-1000:]})
        flash("ClamAV-Signaturen wurden aktualisiert.")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc: flash(f"Signatur-Update fehlgeschlagen: {exc}")
    return redirect(url_for("documents.security_center"))


@bp.post("/<document_id>/relationships")
@login_required
def add_document_relationship(document_id: str):
    _document_or_404(document_id)
    try:
        relation_type = request.form.get("custom_relation_type", "").strip() or request.form.get("relation_type", "related")
        if request.form.get("target", "").strip():
            _store().add_link(
                document_id,
                request.form["target"],
                relation_type,
                request.form.get("label", ""),
                str(g.user["username"]),
                request.form.get("propagates_retention") == "1",
            )
        else:
            _store().add_text_link(document_id, request.form.get("target_text", ""), relation_type, request.form.get("label", ""), str(g.user["username"]))
        flash("Dokumentverknüpfung gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id, link_query=request.form.get("link_query", "")))


@bp.post("/<document_id>/notes")
@login_required
def add_note(document_id: str):
    _document_or_404(document_id)
    try:
        _store().add_note(document_id, request.form.get("text", ""), str(g.user["username"]))
        flash("Notiz wurde als eigene Revision gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/deadlines")
@login_required
def add_document_deadline(document_id: str):
    _document_or_404(document_id)
    try:
        _store().add_deadline(
            document_id,
            request.form.get("kind", "retention"),
            request.form.get("expires_at", ""),
            request.form.get("label", ""),
            str(g.user["username"]),
        )
        flash("Frist wurde nachvollziehbar am Dokument gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.get("/retention")
@login_required
def retention_overview():
    store = _store()
    documents = {item["document_id"]: item for item in store._all_documents()}
    statuses = store.retention_statuses()
    missing = [
        {"document": documents[document_id], "status": status}
        for document_id, status in statuses.items()
        if status["status"] == "deadline_missing"
    ]
    candidates = [
        {
            "document_id": document_id,
            "path": documents[document_id].get("last_path", ""),
            "retention_until": status["retention_until"],
        }
        for document_id, status in statuses.items()
        if status["cleanup_eligible"]
    ]
    return render_template(
        "documents/retention.html",
        candidates=sorted(candidates, key=lambda item: (item["retention_until"], item["path"])),
        missing=sorted(missing, key=lambda item: item["document"].get("last_path", "")),
        folder_rules=store.folder_retention_rules(),
        folders=sorted(
            {".", *(str(Path(item.get("last_path", "")).parent) for item in documents.values())},
            key=str.casefold,
        ),
    )


@bp.post("/retention/rules")
@login_required
def add_retention_rule():
    try:
        _store().add_folder_retention_rule(
            request.form.get("folder", "."),
            request.form.get("kind", "retention"),
            request.form.get("label", ""),
            str(g.user["username"]),
            tag=request.form.get("tag", ""),
            expires_at=request.form.get("expires_at", ""),
            years=request.form.get("years", ""),
        )
        flash("Ordnerregel wurde gespeichert und wird auf Unterordner vererbt.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.retention_overview"))


@bp.post("/retention/rules/<rule_id>/remove")
@login_required
def remove_retention_rule(rule_id: str):
    try:
        _store().remove_folder_retention_rule(
            request.form.get("folder", "."), rule_id, str(g.user["username"])
        )
        flash("Ordnerregel wurde entfernt. Dokumentfristen blieben unverändert.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.retention_overview"))


@bp.post("/retention/cleanup")
@login_required
def run_retention_cleanup():
    if request.form.get("confirm") != "AUSSONDERN":
        flash("Zum Verschieben muss AUSSONDERN eingegeben werden.")
        return redirect(url_for("documents.retention_overview"))
    try:
        result = _store().cleanup_expired(
            request.form.get("destination_folder", ""),
            str(g.user["username"]),
            apply=True,
        )
        flash(f"{len(result['moved'])} Dokument(e) wurden verschoben; nichts wurde gelöscht.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.retention_overview"))


@bp.get("/<document_id>/notes/<note_id>/snapshot.pdf")
@login_required
def download_note_snapshot(document_id: str, note_id: str):
    try:
        path = _store().note_snapshot(document_id, note_id)
        return send_file(path, as_attachment=True, download_name=f"notiz-{note_id}.pdf", mimetype="application/pdf")
    except (OSError, RuntimeError, ValueError):
        abort(404)


@bp.post("/<document_id>/state")
@login_required
def set_state(document_id: str):
    _document_or_404(document_id)
    try:
        _store().set_state(document_id, request.form.get("state", ""), str(g.user["username"]))
        flash("Zustand wurde als eigene Revision gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/move")
@login_required
def move_document(document_id: str):
    try:
        moved = _store().move_document(document_id, request.form.get("destination_folder", ""), str(g.user["username"]))
        flash(f"Dokument verschoben nach {moved['last_path']}. Die Dokument-ID bleibt unverändert.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/share")
@login_required
def create_share(document_id: str):
    _document_or_404(document_id)
    try:
        share = _store().create_share(
            document_id,
            request.form.get("password", ""),
            int(request.form.get("expires_days", "7")),
            str(g.user["username"]),
            request.form.get("note_id", ""),
        )
        flash(f"HTTPS-Link (nur jetzt vollständig sichtbar): {url_for('documents.open_share', share_id=share['share_id'], _external=True)}")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/share/<share_id>/renew")
@login_required
def renew_share(document_id: str, share_id: str):
    _document_or_404(document_id)
    try:
        _store().renew_share(document_id, share_id, request.form.get("password", ""), int(request.form.get("expires_days", "7")), str(g.user["username"]))
        flash("Freigabelink mit neuem Passwort reaktiviert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/offload-versions")
@login_required
def offload_versions(document_id: str):
    _document_or_404(document_id)
    if request.form.get("confirm") != "AUSLAGERN":
        flash("Zum Auslagern muss AUSLAGERN bestätigt werden.")
        return redirect(url_for("documents.detail", document_id=document_id))
    try:
        result = _store().offload_old_versions(document_id, request.form.get("archive_path", ""), str(g.user["username"]))
        flash(f"{len(result['moved_document_ids'])} alte Version(en) auf {result['archive']['label']} ausgelagert. Die aktuelle Version bleibt lokal.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.route("/share/<share_id>", methods=("GET", "POST"))
def open_share(share_id: str):
    if request.method == "GET":
        store = _store(); store.record_share_view(share_id, request.remote_addr or "")
        return render_template("documents/share.html", share_id=share_id, share=store.share_status(share_id))
    try:
        opened = _store().open_share(share_id, request.form.get("password", ""), request.remote_addr or "")
    except ValueError as exc:
        return render_template("documents/share.html", share_id=share_id, share=_store().share_status(share_id), error=str(exc)), 403
    if "note" in opened:
        return render_template("documents/shared_note.html", note=opened["note"], document=opened["document"], share=opened["share"])
    return send_file(opened["path"], as_attachment=True, download_name=opened["path"].name)


@bp.route("/wiki/notes")
@login_required
def notes_wiki():
    return render_template("documents/notes.html", notes=_store().note_wiki())


@bp.route("/logbook")
@login_required
def logbook():
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    filters = {key: request.args.get(key, "").strip() for key in ("q", "actor", "action", "from_at", "to_at")}
    result = _store().logbook_page(page=page, query=filters["q"], actor=filters["actor"], action=filters["action"], from_at=filters["from_at"], to_at=filters["to_at"])
    return render_template("documents/logbook.html", events=result["events"], page=result["page"], has_next=result["has_next"], filters=filters)


@bp.route("/archives")
@login_required
def archives():
    return render_template("documents/archives.html", main_archive=_store().main_archive(), archives=_store().archives())


@bp.post("/archives/register")
@login_required
def register_archive():
    try:
        tags = [tag.strip() for tag in request.form.get("tags", "").split(",") if tag.strip()]
        _store().register_external_archive(request.form.get("path", ""), request.form.get("label", ""), tags, str(g.user["username"]))
        flash("Externes Archiv wurde markiert und registriert.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.archives"))


@bp.post("/archives/discover")
@login_required
def discover_archives():
    _store().discover_archives(str(g.user["username"]))
    flash("Angeschlossene Laufwerke wurden nach Archivmarkern geprüft.")
    return redirect(url_for("documents.archives"))


@bp.route("/sources/ssh")
@login_required
def ssh_sources():
    return render_template("documents/ssh_sources.html", sources=_store().ssh_sources())


@bp.post("/sources/ssh")
@login_required
def register_ssh_source():
    try:
        _store().register_ssh_source(
            request.form.get("name", ""), request.form.get("host", ""), request.form.get("username", ""),
            request.form.get("remote_path", ""), request.form.get("key_path", ""), str(g.user["username"]),
        )
        flash("SSH-Quelle registriert. Für die Synchronisation wird ein SSH-Schlüssel verwendet.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.ssh_sources"))


@bp.post("/sources/ssh/<source_id>/sync")
@login_required
def sync_ssh_source(source_id: str):
    try:
        imported = _store().sync_ssh_source(source_id, str(g.user["username"]))
        flash(f"SSH-Import abgeschlossen: {imported} Datei(en) übernommen.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.ssh_sources"))


@bp.post("/sources/ssh/<source_id>/remove")
@login_required
def remove_ssh_source(source_id: str):
    try:
        _store().remove_ssh_source(source_id, str(g.user["username"]))
        flash("SSH-Quelle entfernt. Auf dem entfernten System und im Archiv wurden keine Dateien gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.ssh_sources"))


@bp.route("/contacts")
@login_required
def contacts():
    actor = str(g.user["username"])
    query = request.args.get("q", "").strip()
    contacts = _contacts().search(query, actor)
    address_values = sorted({address.get("value", "") for contact in contacts for address in contact.get("addresses", []) if address.get("value")}, key=str.casefold)
    carddav_endpoint = url_for("carddav.endpoint", path=f"addressbooks/{g.user['username']}/default/", _external=True)
    return render_template("documents/contacts.html", contacts=contacts, query=query, schema=_contacts().schema(), carddav=_contacts().carddav(), carddav_endpoint=carddav_endpoint, address_matches=_contacts().address_matches(), address_values=address_values)


@bp.get("/forms")
@login_required
def forms():
    definitions = _forms().definitions()
    counts = {item["form_id"]: len(_forms().records(item["form_id"])) for item in definitions}
    return render_template("documents/forms.html", forms=definitions, counts=counts)


@bp.route("/forms/<form_id>", methods=("GET", "POST"))
@login_required
def form_records(form_id: str):
    try:
        form = _forms().definition(form_id)
    except ValueError:
        abort(404)
    if request.method == "POST":
        try:
            _forms().save_record(form_id, request.form.to_dict(), str(g.user["username"]))
            flash(f"{form['name']} gespeichert.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("documents.form_records", form_id=form_id))
    records = _forms().records(form_id)
    return render_template("documents/form_records.html", form=form, records=records,
                           relation_choices=_form_relation_choices(form, str(g.user["username"])),
                           invoice_products=_invoice_products() if form.get("layout") == "invoice" else [])


@bp.route("/forms/<form_id>/<record_id>", methods=("GET", "POST"))
@login_required
def form_record_detail(form_id: str, record_id: str):
    try:
        form = _forms().definition(form_id)
        record = _forms().record(form_id, record_id)
    except ValueError:
        abort(404)
    if request.method == "POST":
        try:
            record = _forms().save_record(form_id, request.form.to_dict(), str(g.user["username"]), record_id)
            flash("Formular gespeichert. Der vorherige Stand bleibt in der Historie.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("documents.form_record_detail", form_id=form_id, record_id=record_id))
    return render_template("documents/form_record_detail.html", form=form, record=record,
                           relation_choices=_form_relation_choices(form, str(g.user["username"])),
                           invoice_products=_invoice_products() if form.get("layout") == "invoice" else [])


@bp.post("/forms/definitions")
@login_required
def save_form_definition():
    try:
        definition = json.loads(request.form.get("definition", "{}"))
        form = _forms().save_definition(definition, str(g.user["username"]))
        flash(f"Formularvorlage {form['name']} gespeichert.")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        flash(f"Formularvorlage ungültig: {exc}")
    return redirect(url_for("documents.forms"))


@bp.get("/contacts/<contact_id>")
@login_required
def contact_detail(contact_id: str):
    actor = str(g.user["username"])
    try:
        contact = _contacts().get(contact_id, actor)
    except ValueError:
        abort(404)
    users = [row["username"] for row in get_db().execute("SELECT username FROM user ORDER BY username COLLATE NOCASE").fetchall()]
    return render_template("documents/contact_detail.html", contact=contact, users=users, is_owner=not contact.get("owner") or contact.get("owner") == actor)


@bp.post("/contacts")
@login_required
def save_contact():
    contact_id = request.form.get("contact_id", "")
    try:
        contact = _contacts().upsert(request.form.to_dict(), str(g.user["username"]), contact_id)
        flash("Kontakt gespeichert.")
    except ValueError as exc:
        flash(str(exc))
        contact = None
    if contact_id and contact is not None:
        return redirect(url_for("documents.contact_detail", contact_id=contact["contact_id"]))
    return redirect(url_for("documents.contacts"))


@bp.get("/contacts/export.vcf")
@login_required
def export_contacts():
    payload = _contacts().export_vcards(str(g.user["username"])).encode("utf-8")
    return send_file(io.BytesIO(payload), as_attachment=True, download_name="simpleoffice-kontakte.vcf", mimetype="text/vcard; charset=utf-8")


@bp.post("/contacts/import")
@login_required
def import_contacts():
    uploaded = request.files.get("contacts_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .vcf-Datei auswählen.")
        return redirect(url_for("documents.contacts"))
    try:
        imported = _contacts().import_vcards(uploaded.read().decode("utf-8-sig"), str(g.user["username"]))
        flash(f"{imported} Kontakt(e) importiert.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kontaktimport fehlgeschlagen: {exc}")
    return redirect(url_for("documents.contacts"))


@bp.post("/contacts/<contact_id>/addresses")
@login_required
def add_contact_address(contact_id: str):
    try:
        _contacts().add_address(contact_id, request.form.get("label", ""), request.form.get("address", ""), str(g.user["username"]))
        flash("Adresse gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/sharing")
@login_required
def share_contact(contact_id: str):
    actor = str(g.user["username"])
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    managers = request.form.getlist("managers")
    unknown = sorted(set(managers) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        _contacts().share(contact_id, managers, actor)
        flash("Verwaltungsfreigabe gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


@bp.post("/contacts/schema")
@login_required
def save_contact_schema():
    try:
        aliases = json.loads(request.form.get("aliases", "{}"))
        if not isinstance(aliases, dict) or not all(isinstance(value, list) for value in aliases.values()):
            raise ValueError("aliases must be a JSON object whose values are lists")
        _contacts().save_schema(request.form.get("required", "").split(","), aliases, str(g.user["username"]))
        flash("Kontaktfeld-Zuordnung gespeichert.")
    except (json.JSONDecodeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.contacts"))


@bp.post("/contacts/carddav")
@login_required
def activate_carddav():
    try:
        _contacts().activate_carddav(str(g.user["username"]), request.form.get("password", ""), str(g.user["username"]))
        endpoint = url_for("carddav.endpoint", path=f"addressbooks/{g.user['username']}/default/", _external=True)
        flash(f"CardDAV aktiviert. Thunderbird-URL: {endpoint}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contacts"))


@bp.route("/calendar")
@login_required
def calendar():
    actor = str(g.user["username"])
    reminder_now = datetime.now(timezone.utc)
    try:
        reminders = _calendar().due_alarms(actor, reminder_now - timedelta(hours=12), reminder_now + timedelta(days=7))
    except ValueError as exc:
        reminders = []
        flash(f"Erinnerungen konnten nicht berechnet werden: {exc}")
    calendars = _calendars().calendars(actor)
    calendar_map = {item["calendar_id"]: item for item in calendars}
    requested_month = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        shown_month = date.fromisoformat(f"{requested_month}-01")
    except ValueError:
        shown_month = date.today().replace(day=1)
    events_by_day: dict[int, list[dict]] = {}
    events = [event for event in _calendar().events(actor) if event.get("status", "active") not in {"cancelled", "deleted", "moved"}]
    month_lower = datetime(shown_month.year, shown_month.month, 1, tzinfo=timezone.utc)
    next_month = (shown_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_upper = datetime(next_month.year, next_month.month, 1, tzinfo=timezone.utc)
    try:
        occurrences = _calendar().occurrences(actor, month_lower, month_upper)
    except ValueError as exc:
        occurrences = []; flash(f"Serientermine konnten nicht dargestellt werden: {exc}")
    for event in events:
        collection = calendar_map.get(event.get("calendar_id") or "default", {"name": "Persönlich", "color": "#2563eb"})
        event["calendar_name"] = collection["name"]; event["calendar_color"] = collection["color"]
    for event in occurrences:
        collection = calendar_map.get(event.get("calendar_id") or "default", {"name": "Persönlich", "color": "#2563eb"})
        event["calendar_name"] = collection["name"]; event["calendar_color"] = collection["color"]
        try:
            event_day = datetime.fromisoformat(event["start"].replace("Z", "+00:00")).date()
        except (KeyError, ValueError):
            continue
        if event_day.year == shown_month.year and event_day.month == shown_month.month:
            events_by_day.setdefault(event_day.day, []).append(event)
    previous = (shown_month.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    following = (shown_month.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m")
    users = [row["username"] for row in get_db().execute("SELECT username FROM user ORDER BY username COLLATE NOCASE").fetchall()]
    for event in events:
        # Events created before calendar sharing was introduced do not have
        # these fields.  Normalize only the in-memory view so opening the
        # calendar stays backwards compatible without rewriting user data.
        event["access"] = event.get("access") if isinstance(event.get("access"), dict) else {}
        event["managers"] = event.get("managers") if isinstance(event.get("managers"), list) else []
        if event.get("requester_email") or event.get("source") == "external_booking":
            event["origin"] = "external"; event["origin_label"] = "Externe Buchung"; event["origin_class"] = "text-bg-warning"
        elif event.get("source_uid") or event.get("source") == "ical_import":
            event["origin"] = "imported"; event["origin_label"] = "Importiert"; event["origin_class"] = "text-bg-secondary"
        elif event.get("owner") and event.get("owner") != actor:
            event["origin"] = "shared"; event["origin_label"] = f"Von {event['owner']}"; event["origin_class"] = "text-bg-info"
        else:
            event["origin"] = "own"; event["origin_label"] = "Von mir angelegt"; event["origin_class"] = "text-bg-primary"
        event["can_edit"] = _calendar()._can_edit(event, actor)
        event["is_owner"] = (event.get("owner") or actor) == actor
        event["access_role"] = "owner" if event["is_owner"] else event.get("access", {}).get(actor, "edit" if actor in event.get("managers", []) else "read")
        if event.get("status") == "confirmed" and event.get("requester_email"):
            ics_url = url_for("documents.download_booking_confirmation", event_id=event["event_id"], _external=True)
            subject = f"Terminbestätigung: {event['title']}"
            body = f"Hallo {event.get('requester_name') or ''},\n\ndein Termin wurde bestätigt. Die Kalendereinladung kannst du hier herunterladen:\n{ics_url}\n"
            event["confirmation_mailto"] = "mailto:" + event["requester_email"] + "?" + urlencode({"subject": subject, "body": body})
    return render_template("documents/calendar.html", events=events, calendars=calendars, contacts=_contacts().contacts(actor), users=users, current_username=actor, current_user_email=str(g.user["email"] or ""), local_calendar_address=local_calendar_address(actor), scheduling_access=_scheduling_access().get(actor), google_sync=_google_calendar().status(actor), booking=_calendar().booking_settings(), pending=_calendar().pending_bookings(), itip_messages=_itip().messages(actor), reminders=reminders, reminder_now=reminder_now.isoformat(timespec="seconds"), defaults=_settings().settings(), calendar_weeks=monthcalendar(shown_month.year, shown_month.month), calendar_events=events_by_day, shown_month=shown_month.strftime("%Y-%m"), shown_month_name=f"{month_name[shown_month.month]} {shown_month.year}", previous_month=previous, following_month=following)


@bp.post("/calendar/google/preview")
@login_required
def preview_google_calendar_sync():
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=False)
        flash(f"Google-Vorschau: {result['received']} Änderungen empfangen, {result['applicable']} anwendbar, {len(result['conflicts'])} Konflikte. Kalenderdaten und Sync-Token blieben unverändert.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Kalender konnte nicht geprüft werden: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/sync")
@login_required
def apply_google_calendar_sync():
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=True)
        if result["conflicts"]:
            flash(f"Google-Abgleich: {result['applied']} Änderungen gespeichert; {len(result['conflicts'])} lokale Konflikte blieben unverändert. Bitte zuerst manuell auflösen.")
        else:
            flash(f"Google-Abgleich abgeschlossen: {result['applied']} Änderungen gespeichert, keine Konflikte.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Kalender wurde nicht geändert: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/conflicts/<strategy>")
@login_required
def resolve_google_calendar_conflicts(strategy: str):
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=True, conflict_policy=strategy)
        flash(f"Google-Konflikte aufgelöst: {result['applied']} Google-Versionen übernommen, {result['kept_local']} lokale Versionen beibehalten.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Konflikte wurden nicht aufgelöst: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/reset")
@login_required
def reset_google_calendar_sync():
    _google_calendar().disable(str(g.user["username"]))
    flash("Google-Sync-Zustand entfernt. Importierte Termine bleiben erhalten; der nächste Abgleich prüft den Kalender vollständig.")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/scheduling/import")
@login_required
def import_itip_message():
    uploaded = request.files.get("itip_file")
    try:
        if uploaded is None or not uploaded.filename:
            raise ValueError("Bitte eine iTIP-/ICS-Datei auswählen.")
        payload = uploaded.stream.read(MAX_MESSAGE_BYTES + 1)
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError("iTIP message exceeds 1 MiB")
        message = _itip().receive(payload.decode("utf-8-sig"), str(g.user["username"]), "file-import")
        flash(f"{message['method']}-Nachricht geprüft und zur Bestätigung vorgemerkt.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Termin-Nachricht abgewiesen: {exc}")
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.post("/calendar/scheduling/<message_id>/apply")
@login_required
def apply_itip_message(message_id: str):
    try:
        _itip().apply(message_id, str(g.user["username"]), request.form.get("calendar_id", "default"))
        flash("Termin-Nachricht angewendet und revisionssicher protokolliert.")
    except (ItipConflict, ValueError) as exc:
        flash(f"Termin-Nachricht konnte nicht angewendet werden: {exc}")
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.post("/calendar/scheduling/<message_id>/reject")
@login_required
def reject_itip_message(message_id: str):
    try:
        _itip().reject(message_id, str(g.user["username"]), request.form.get("reason", ""))
        flash("Termin-Nachricht abgelehnt; Kalenderdaten blieben unverändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.get("/calendar/<event_id>/scheduling.ics")
@login_required
def export_itip_message(event_id: str):
    method = request.args.get("method", "REQUEST")
    try:
        payload = _itip().export(event_id, str(g.user["username"]), method, request.args.get("attendee", ""), request.args.get("partstat", ""), str(g.user["email"] or ""))
    except ValueError as exc:
        return Response(str(exc), 403, {"Content-Type": "text/plain; charset=utf-8"})
    return send_file(io.BytesIO(payload.encode()), as_attachment=True, download_name=f"termin-{method.casefold()}-{event_id}.ics", mimetype=f"text/calendar; method={method.upper()}; charset=utf-8")


@bp.post("/calendar/scheduling/access")
@login_required
def update_caldav_scheduling_access():
    actor = str(g.user["username"])
    users = {str(row["username"]) for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        _scheduling_access().update(
            actor,
            request.form.get("enabled") == "1",
            [username for username in users if request.form.get(f"messages_{username}") == "1"],
            [username for username in users if request.form.get(f"freebusy_{username}") == "1"],
            users,
        )
        flash("CalDAV-Terminplanung und Freigaben wurden gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#scheduling-access")


@bp.post("/calendar/caldav")
@login_required
def activate_caldav():
    actor = str(g.user["username"])
    try:
        _calendars().activate(actor, request.form.get("password", ""), actor)
        flash(f"CalDAV aktiviert. Thunderbird-URL: {url_for('caldav.endpoint', path='', _external=True)}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.post("/calendar/collections")
@login_required
def create_calendar_collection():
    actor = str(g.user["username"])
    try:
        _calendars().create(request.form.get("name", ""), actor, request.form.get("color", "#2563eb"), request.form.get("timezone", "Europe/Berlin"), request.form.get("description", ""))
        flash("Kalender angelegt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.post("/calendar/collections/<calendar_id>/sharing")
@login_required
def share_calendar_collection(calendar_id: str):
    actor = str(g.user["username"]); valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        _calendars().update_sharing(calendar_id, {user: request.form.get(f"access_{user}", "") for user in valid_users}, actor)
        flash("Kalenderfreigaben gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.get("/calendar/export.ics")
@login_required
def export_calendar():
    payload = _calendar().export_ics(str(g.user["username"])).encode("utf-8")
    return send_file(io.BytesIO(payload), as_attachment=True, download_name="simpleoffice-kalender.ics", mimetype="text/calendar; charset=utf-8")


@bp.post("/calendar/import")
@login_required
def import_calendar():
    uploaded = request.files.get("calendar_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .ics-Datei auswählen.")
        return redirect(url_for("documents.calendar"))
    try:
        imported = _calendar().import_ics(uploaded.read().decode("utf-8-sig"), str(g.user["username"]))
        flash(f"{imported} Kalendertermin(e) importiert.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kalenderimport fehlgeschlagen: {exc}")
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/import/preview")
@login_required
def preview_calendar_import():
    uploaded = request.files.get("calendar_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .ics-Datei auswählen.")
        return redirect(url_for("documents.calendar") + "#calendar-import")
    try:
        payload = uploaded.stream.read(MAX_PREVIEW_BYTES + 1)
        if len(payload) > MAX_PREVIEW_BYTES:
            raise ValueError(f"iCalendar preview is limited to {MAX_PREVIEW_BYTES // 1024} KiB")
        preview = preview_ics(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kalendervorschau fehlgeschlagen: {exc}")
        return redirect(url_for("documents.calendar") + "#calendar-import")
    return render_template(
        "documents/calendar_import_preview.html",
        preview=preview,
        filename=uploaded.filename,
    )


@bp.post("/calendar")
@login_required
def add_calendar_event():
    actor = str(g.user["username"])
    owner = request.form.get("owner", actor).strip() or actor
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        if owner not in valid_users:
            raise ValueError("unknown owner")
        calendar_id = request.form.get("calendar_id", "default")
        _calendars().get(calendar_id, actor, write=True)
        metadata = {**_calendar_metadata(), "description_html": request.form.get("description_html", ""), "description_format": request.form.get("description_format", "text")}
        event = _calendar().add(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), owner, calendar_id, metadata)
        if request.form.get("rrule", "").strip() or request.form.get("rdates", "").strip():
            event = _calendar().set_recurrence(event["event_id"], {"rrule": request.form.get("rrule", ""), "rdates": request.form.get("rdates", "").splitlines(), "exdates": request.form.get("exdates", "").splitlines(), "timezone": request.form.get("recurrence_timezone", "Europe/Berlin")}, actor, event.get("updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Kalendertermin gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>")
@login_required
def update_calendar_event(event_id: str):
    try:
        actor = str(g.user["username"]); calendar_id = request.form.get("calendar_id", "")
        if calendar_id: _calendars().get(calendar_id, actor, write=True)
        source_calendar_id = _calendar().get(event_id, actor).get("calendar_id") or "default"
        metadata = {**_calendar_metadata(), "description_html": request.form.get("description_html", ""), "description_format": request.form.get("description_format", "text")}
        event = _calendar().update(event_id, request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), calendar_id, metadata)
        _calendars().record_event_move(event, source_calendar_id, actor)
        flash("Kalendertermin geändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>/participants")
@login_required
def update_calendar_participants(event_id: str):
    participants = []
    try:
        for line in request.form.get("participants", "").splitlines():
            if not line.strip(): continue
            email, name, role, status, rsvp = (line.split("|") + ["", "", "", "", ""])[:5]
            participants.append({"email": email.strip(), "name": name.strip(), "role": role.strip() or "required", "status": status.strip() or "needs-action", "rsvp": rsvp.strip().lower() in {"1", "true", "ja", "yes"}})
        _calendar().set_participants(event_id, participants, str(g.user["username"]))
        flash("Teilnehmer gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/recurrence")
@login_required
def update_calendar_recurrence(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); calendar_id = previous.get("calendar_id") or "default"
        event = _calendar().set_recurrence(event_id, {"rrule": request.form.get("rrule", ""), "rdates": request.form.get("rdates", "").splitlines(), "exdates": request.form.get("exdates", "").splitlines(), "timezone": request.form.get("recurrence_timezone", "")}, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Serienregel gespeichert und für CalDAV synchronisiert.")
    except ValueError as exc:
        flash(f"Serienregel nicht gespeichert: {exc}")
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/occurrence")
@login_required
def update_calendar_occurrence(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); calendar_id = previous.get("calendar_id") or "default"
        event = _calendar().set_occurrence_exception(event_id, request.form.get("recurrence_id", ""), actor, status=request.form.get("occurrence_status", "active"), start=request.form.get("occurrence_start", ""), end=request.form.get("occurrence_end", ""), title=request.form.get("occurrence_title", ""), reason=request.form.get("occurrence_reason", ""), expected_updated_at=request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Einzelne Serieninstanz revisionssicher geändert.")
    except ValueError as exc:
        flash(f"Serieninstanz nicht geändert: {exc}")
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/alarms")
@login_required
def add_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        minutes = int(request.form.get("minutes", "15"))
        if not 0 <= minutes <= 527040:
            raise ValueError("Erinnerungsabstand muss zwischen 0 und 527040 Minuten liegen.")
        direction = request.form.get("direction", "before")
        related = request.form.get("related", "start")
        if direction not in {"before", "after"} or related not in {"start", "end"}:
            raise ValueError("Ungültiger Erinnerungsbezug.")
        alarms = list(previous.get("alarms", []))
        alarms.append({"action": "DISPLAY", "description": request.form.get("description", "").strip() or previous.get("title", "Erinnerung"), "trigger": {"kind": "relative", "seconds": minutes * 60 * (-1 if direction == "before" else 1), "related": related}})
        event = _calendar().set_alarms(event_id, alarms, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Lokale Kalendererinnerung gespeichert und für CalDAV synchronisiert.")
    except (TypeError, ValueError) as exc:
        flash(f"Erinnerung nicht gespeichert: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/delete")
@login_required
def delete_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); alarm_uid = request.form.get("alarm_uid", "")
        alarms = [item for item in previous.get("alarms", []) if item.get("uid") != alarm_uid]
        if len(alarms) == len(previous.get("alarms", [])):
            raise ValueError("Unbekannte Kalendererinnerung.")
        event = _calendar().set_alarms(event_id, alarms, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Kalendererinnerung entfernt.")
    except ValueError as exc:
        flash(f"Erinnerung nicht entfernt: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/acknowledge")
@login_required
def acknowledge_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        event = _calendar().acknowledge_alarm(event_id, request.form.get("alarm_uid", ""), actor)
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Erinnerung bestätigt.")
    except ValueError as exc:
        flash(f"Erinnerung nicht bestätigt: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/snooze")
@login_required
def snooze_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        event = _calendar().snooze_alarm(event_id, request.form.get("alarm_uid", ""), actor, int(request.form.get("minutes", "10")))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Erinnerung wurde verschoben.")
    except (TypeError, ValueError) as exc:
        flash(f"Erinnerung nicht verschoben: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.get("/calendar/reminders.json")
@login_required
def calendar_reminders_json():
    now = datetime.now(timezone.utc)
    try:
        lower = datetime.fromisoformat(request.args.get("from", "").replace("Z", "+00:00")) if request.args.get("from") else now - timedelta(hours=12)
        upper = datetime.fromisoformat(request.args.get("to", "").replace("Z", "+00:00")) if request.args.get("to") else now + timedelta(days=7)
        rows = _calendar().due_alarms(str(g.user["username"]), lower, upper, request.args.get("calendar_id", ""))
        return Response(json.dumps({"generated_at": now.isoformat(timespec="seconds"), "reminders": rows}, ensure_ascii=False), mimetype="application/json")
    except ValueError as exc:
        return Response(json.dumps({"error": str(exc)}, ensure_ascii=False), 400, mimetype="application/json")


@bp.post("/calendar/<event_id>/delete")
@login_required
def delete_calendar_event(event_id: str):
    try:
        _calendar().delete(event_id, str(g.user["username"]))
        flash("Kalendertermin gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>/sharing")
@login_required
def share_calendar_event(event_id: str):
    actor = str(g.user["username"])
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    permissions = {username: request.form.get(f"access_{username}", "") for username in valid_users}
    unknown = sorted(set(request.form.getlist("users")) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        _calendar().share(event_id, permissions, actor)
        flash("Lesen- und Bearbeitungsrechte für den Termin gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.get("/calendar/published/<audience>")
def published_calendar(audience: str):
    try:
        return render_template("documents/published_calendar.html", audience=audience, events=_calendar().visible_events(audience))
    except ValueError:
        abort(404)


@bp.post("/calendar/booking-settings")
@login_required
def save_booking_settings():
    try:
        _calendar().save_booking_settings(request.form.get("enabled") == "1", int(request.form.get("duration_minutes", "60")), request.form.get("start_time", "09:00"), request.form.get("end_time", "17:00"), str(g.user["username"]), request.form.get("timezone", "Europe/Berlin"))
        flash("Externe Buchungseinstellungen gespeichert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/bookings/<event_id>/confirm")
@login_required
def confirm_booking(event_id: str):
    try:
        event = _calendar().confirm_booking(event_id, str(g.user["username"]))
        if event.get("confirmation_delivery", {}).get("status") == "sent":
            flash("Buchung bestätigt und ICS-E-Mail versendet.")
        else:
            flash("Buchung bestätigt und verbindlich blockiert. E-Mail-Versand ist ausstehend; die ICS-Datei kann im Termin heruntergeladen werden.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.get("/calendar/bookings/<event_id>/confirmation.ics")
@login_required
def download_booking_confirmation(event_id: str):
    try:
        payload = _calendar().booking_ics(event_id, str(g.user["username"])).encode("utf-8")
    except ValueError:
        abort(404)
    return send_file(io.BytesIO(payload), as_attachment=True, download_name=f"terminbestaetigung-{event_id}.ics", mimetype="text/calendar; charset=utf-8")


@bp.route("/calendar/book", methods=("GET", "POST"))
def book_calendar_slot():
    from datetime import date
    selected_day = request.values.get("date", date.today().isoformat())
    try:
        slots = _calendar().available_slots(date.fromisoformat(selected_day))
        if request.method == "POST":
            _calendar().request_booking(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("name", ""), request.form.get("email", ""), request.form.get("start", ""), request.form.get("end", ""))
            return render_template("documents/book_calendar.html", date=selected_day, slots=slots, sent=True)
        return render_template("documents/book_calendar.html", date=selected_day, slots=slots)
    except ValueError as exc:
        return render_template("documents/book_calendar.html", date=selected_day, slots=[], error=str(exc)), 400


@bp.get("/contacts/<contact_id>.vcf")
@login_required
def download_contact_vcard(contact_id: str):
    try:
        card = _contacts().vcard(contact_id, str(g.user["username"]))
    except ValueError:
        abort(404)
    return Response(card, mimetype="text/vcard", headers={"Content-Disposition": f'attachment; filename="contact-{contact_id}.vcf"'})
