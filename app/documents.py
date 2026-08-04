"""Authenticated web pages for document versions, notes and audit history."""

from __future__ import annotations

import io
import json
import mimetypes
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from calendar import month_name, monthcalendar
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .document_store import DocumentStore
from .contact_store import ContactStore
from .calendar_store import CalendarStore
from .calendar_collections import CalendarCollections
from .ics_preview import MAX_PREVIEW_BYTES, preview_ics
from .todo_store import TodoStore
from .settings_store import SettingsStore
from .form_store import FormStore
from .project_store import ProjectStore
from .replication_store import CATEGORIES, ReplicationStore
from .object_store import ObjectStore
from .db import get_db


bp = Blueprint("documents", __name__, url_prefix="/documents")


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _contacts() -> ContactStore:
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _calendar() -> CalendarStore:
    return CalendarStore(current_app.config["DOCUMENT_ROOT"])


def _calendars() -> CalendarCollections:
    return CalendarCollections(current_app.config["DOCUMENT_ROOT"])


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
    devices=[]
    for mount in _store()._mounted_roots():
        try:
            usage=shutil.disk_usage(mount); devices.append({"path":str(mount),"total":usage.total,"free":usage.free})
        except OSError: pass
    return {"time": datetime.now().astimezone(), "root": str(root), "storage": storage, "devices": devices}


def _calendar_tags() -> list[dict[str, str]]:
    return [
        {"name": tag.strip(), "visibility": visibility}
        for visibility in ("private", "family", "external")
        for tag in request.form.get(f"{visibility}_tags", "").split(",")
        if tag.strip()
    ]


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
    return render_template("documents/index.html", documents=documents, document_tree=_document_tree(documents), defaults=_settings().settings(), **result)


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
    documents = _store().list_documents()
    return render_template("documents/dashboard.html", system=_system_overview(), inbox=[item for item in documents if _is_unprocessed(item)], todos=_todos().items(), pending=_calendar().pending_bookings(), scan_status=_store().scan_status())


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
    documents = [item for item in _store().list_documents() if _is_unprocessed(item)]
    return render_template("documents/index.html", documents=documents, document_tree=_document_tree(documents), inbox_only=True, defaults=_settings().settings(), page=1, page_size=max(1, len(documents)), total=len(documents), has_next=False)


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
    linked_documents = {item["document_id"]: item for item in store._all_documents()}
    relationships = [{**relationship, "target": linked_documents.get(relationship.get("target_document_id"))} for relationship in document.get("relationships", [])]
    return render_template(
        "documents/detail.html",
        document=document,
        versions=store.versions(document_id),
        logbook=store.logbook(document_id),
        relationships=relationships,
        shares=store.document_shares(document_id),
        retention=store.retention_status(document_id),
        link_query=query,
        link_matches=[item for item in store.find_matches(query) if item["document_id"] != document_id] if query else [],
        preview={**_preview_data(document), "url": url_for("documents.image_preview", document_id=document_id), "name": document.get("last_path", "").rsplit("/", 1)[-1], "text": (document.get("extracted_text") or document.get("ocr_text") or "")[:12000]},
        defaults=_settings().settings(),
    )


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
    calendars = _calendars().calendars(actor)
    calendar_map = {item["calendar_id"]: item for item in calendars}
    requested_month = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        shown_month = date.fromisoformat(f"{requested_month}-01")
    except ValueError:
        shown_month = date.today().replace(day=1)
    events_by_day: dict[int, list[dict]] = {}
    events = [event for event in _calendar().events(actor) if event.get("status", "active") not in {"cancelled", "deleted", "moved"}]
    for event in events:
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
    return render_template("documents/calendar.html", events=events, calendars=calendars, contacts=_contacts().contacts(actor), users=users, current_username=actor, booking=_calendar().booking_settings(), pending=_calendar().pending_bookings(), defaults=_settings().settings(), calendar_weeks=monthcalendar(shown_month.year, shown_month.month), calendar_events=events_by_day, shown_month=shown_month.strftime("%Y-%m"), shown_month_name=f"{month_name[shown_month.month]} {shown_month.year}", previous_month=previous, following_month=following)


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
        _calendar().add(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), owner, calendar_id)
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
        event = _calendar().update(event_id, request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), calendar_id)
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
        _calendar().save_booking_settings(request.form.get("enabled") == "1", int(request.form.get("duration_minutes", "60")), request.form.get("start_time", "09:00"), request.form.get("end_time", "17:00"), str(g.user["username"]))
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
