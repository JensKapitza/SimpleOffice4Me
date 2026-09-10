"""Document HTTP route group extracted from app.documents."""
from __future__ import annotations

from .documents_core import *  # noqa: F401,F403

@bp.route("/")
@login_required
def index():
    try: page = max(1, int(request.args.get("page", "1")))
    except ValueError: page = 1
    result = _store().document_page(page=page)
    documents = result["documents"]
    quick_query = request.args.get("quick", "").strip()
    quick_results, quick_error = [], ""
    if quick_query:
        try:
            # JSON quoting produces a parser-safe phrase even for umlauts,
            # whitespace and operator-looking input supplied by a user.
            literal = json.dumps(quick_query + "*", ensure_ascii=False)
            quick_results = _store().search_page(
                f"name: {literal} ODER tag: {literal}", page_size=25
            )["results"]
        except ValueError as exc:
            quick_error = str(exc)
    return render_template(
        "documents/index.html",
        document_tree=_document_tree(documents),
        defaults=_settings().settings(),
        quick_query=quick_query,
        quick_results=quick_results,
        quick_error=quick_error,
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
    error = ""
    if query:
        # Never scan or backfill here: an initial scan may take a long time and
        # runs independently in the launcher.  The existing index can answer
        # immediately and is extended as the background scan progresses.
        try:
            result = _store().search_page(query, page=page)
            for item in result["results"]:
                _store().record_access(item["document_id"], str(g.user["username"]), "found")
        except ValueError as exc:
            error = str(exc)
    return render_template(
        "documents/search.html", query=query, error=error,
        scan_status=_store().scan_status(), **result
    )


@bp.get("/quick-search")
@login_required
def quick_search():
    """Search the main registers and jump directly for one unambiguous hit."""
    query = request.args.get("q", "").strip()
    matches: list[dict[str, str]] = []
    if query:
        needle = query.casefold()
        try:
            literal = json.dumps(query + "*", ensure_ascii=False)
            rows = _store().search_page(f"name: {literal} ODER tag: {literal}", page_size=12)["results"]
        except ValueError:
            rows = []
        matches.extend({
            "kind": "document", "label": row["path"], "detail": row.get("state", ""),
            "url": url_for("documents.detail", document_id=row["document_id"]),
        } for row in rows)
        matches.extend({
            "kind": "object", "label": row.get("name", row["object_id"]), "detail": row.get("display_id", ""),
            "url": url_for("documents.object_detail", object_id=row["object_id"]),
        } for row in _objects().objects(query)[:12])
        project_rows = [row for row in _projects().projects() if needle in json.dumps(row, ensure_ascii=False).casefold()][:12]
        matches.extend({
            "kind": "project", "label": row.get("title", row["project_id"]), "detail": row.get("status", ""),
            "url": url_for("documents.project_detail", project_id=row["project_id"]),
        } for row in project_rows)
        actor = str(g.user["username"])
        matches.extend({
            "kind": "contact", "label": row.get("fields", {}).get("display_name", row["contact_id"]),
            "detail": row.get("fields", {}).get("company", ""),
            "url": url_for("documents.contact_detail", contact_id=row["contact_id"]),
        } for row in _contacts().search(query, actor)[:12])

        # Local import avoids a blueprint startup cycle.
        from .business_documents import invoices
        contacts = _contacts()
        for row in invoices(_store().root):
            searchable = " ".join((str(row.get("invoice_number", "")), str(row.get("buyer", {}).get("name", "")))).casefold()
            if needle not in searchable or not contacts.can_manage(row.get("contact_id", ""), actor):
                continue
            matches.append({
                "kind": "invoice", "label": row.get("invoice_number", row["invoice_id"]),
                "detail": row.get("buyer", {}).get("name", ""),
                "url": url_for("contact_audit.business_documents.customer_billing", contact_id=row["contact_id"]),
            })
        matches = matches[:50]
        if len(matches) == 1:
            return redirect(matches[0]["url"])
    return render_template("documents/quick_search.html", query=query, matches=matches)


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
    from .business_documents import customer_account_overview

    inbox = _store().inbox_page(page=1, page_size=8)
    return render_template(
        "documents/dashboard.html",
        system=_system_overview(),
        inbox=inbox["documents"],
        inbox_total=inbox["total"],
        todos=_todos().items(str(g.user["username"])),
        pending=_calendar().pending_bookings(),
        scan_status=_store().scan_status(),
        setup_status=_setup().status(str(g.user["username"])),
        customer_accounts=customer_account_overview(_store().root, str(g.user["username"])),
    )


@bp.get("/setup")
@login_required
def first_run_setup():
    username = str(g.user["username"])
    return render_template("documents/setup.html", setup=_setup().status(username), remote=_remote_setup_context(username), credentials=None)


@bp.post("/setup/access")
@login_required
def first_run_access():
    """Create separate one-time credentials for all three DAV services."""
    from .webdav import activate

    username = str(g.user["username"])
    if not _remote_setup_context(username)["secure_transport"]:
        return Response("HTTPS ist vor dem Erzeugen von App-Passwörtern erforderlich.\n", 400, content_type="text/plain; charset=utf-8")
    credentials = {
        "webdav": activate(username, username, label="Erststart: Dateien und SFTP", scope="write", expires_days=365),
        "caldav": secrets.token_urlsafe(24),
        "carddav": secrets.token_urlsafe(24),
    }
    _calendars().activate(username, credentials["caldav"], username)
    _contacts().activate_carddav(username, credentials["carddav"], username)
    flash("Drei getrennte App-Passwörter wurden erzeugt. Sie werden nur auf dieser Seite angezeigt.")
    return render_template("documents/setup.html", setup=_setup().status(username), remote=_remote_setup_context(username), credentials=credentials)


@bp.post("/setup/complete")
@login_required
def complete_first_run_setup():
    username = str(g.user["username"])
    try:
        _setup().complete(username, request.form.get("platform", "windows"), username)
        flash("Erststart abgeschlossen. Der Assistent bleibt über Einstellungen erreichbar.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.first_run_setup"))


@bp.get("/setup/export.txt")
@login_required
def export_first_run_setup():
    remote = _remote_setup_context(str(g.user["username"]))
    text = render_template("documents/setup_export.txt", remote=remote)
    return Response(text, content_type="text/plain; charset=utf-8", headers={"Content-Disposition": "attachment; filename=SimpleOffice-Einrichtung.txt"})


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
    actor = str(g.user["username"])
    try:
        if request.method == "POST":
            _projects().update_project(project_id, request.form.to_dict(), str(g.user["username"]))
            flash("Projekt gespeichert.")
            return redirect(url_for("documents.project_detail", project_id=project_id))
        project = _projects().project(project_id)
    except ValueError as exc:
        flash(str(exc)); return redirect(url_for("documents.projects"))
    _todos().migrate_project_tasks([project], actor)
    project = {**project, "tasks": _todos().project_tasks(project_id, actor)}
    linked_documents = []
    for document_id in project.get("document_ids", []):
        try:
            linked_documents.append(_store().get_document(document_id))
        except ValueError:
            continue
    return render_template(
        "documents/project_detail.html", project=project, linked_documents=linked_documents,
        billing=_projects().billing_projection(project_id, actor, project["tasks"]),
        available_time_group_entries=_projects().available_time_group_entries(project_id, project["tasks"]),
    )


@bp.post("/projects/<project_id>/tasks")
@login_required
def add_project_task(project_id: str):
    try:
        actor = str(g.user["username"]); project = _projects().project(project_id); _todos().migrate_project_tasks([project], actor)
        values = request.form.to_dict(); values["predecessors"] = request.form.getlist("predecessors"); values.update({"project_id": project_id, "list_id": "project-" + project_id, "start": values.get("planned_start", ""), "due": values.get("planned_end", ""), "assigned_to": request.form.getlist("resources") or values.get("resources", ""), "status": {"open": "needs-action", "in_progress": "in-process", "waiting": "in-process"}.get(values.get("status", "open"), values.get("status"))})
        _todos().add(values.get("title", ""), actor, values)
        flash("Aufgabe angelegt.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + "#aufgaben")


@bp.post("/projects/<project_id>/tasks/<task_id>")
@login_required
def update_project_task(project_id: str, task_id: str):
    try:
        values = request.form.to_dict(); values["predecessors"] = request.form.getlist("predecessors"); values.update({"project_id": project_id, "start": values.get("planned_start", ""), "due": values.get("planned_end", ""), "assigned_to": request.form.getlist("resources") or values.get("resources", ""), "status": {"open": "needs-action", "in_progress": "in-process", "waiting": "in-process"}.get(values.get("status", "open"), values.get("status"))})
        _todos().update(task_id, values, str(g.user["username"]))
        flash("Aufgabe gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + f"#task-{task_id}")


@bp.post("/projects/<project_id>/tasks/<task_id>/time")
@login_required
def book_project_task_time(project_id: str, task_id: str):
    try:
        minutes = ProjectStore._duration_minutes(request.form.get("hours", ""), request.form.get("minutes"))
        entry = _todos().book_time(task_id, minutes, request.form.get("note", ""), str(g.user["username"]), request.form.get("date", ""))
        flash(f"{entry['minutes'] // 60}:{entry['minutes'] % 60:02d} Stunden gebucht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + f"#task-{task_id}")


@bp.post("/projects/<project_id>/time-groups")
@login_required
def create_project_time_group(project_id: str):
    try:
        actor = str(g.user["username"]); task_rows = _todos().project_tasks(project_id, actor)
        _projects().create_time_group(project_id, {
            "title": request.form.get("title", ""),
            "invoice_text": request.form.get("invoice_text", ""),
            "hours": request.form.get("hours", ""),
            "minutes": request.form.get("minutes", ""),
            "entry_ids": request.form.getlist("entry_ids"),
        }, actor, task_rows)
        flash("Abrechnungsgruppe wurde angelegt. Einzelzeiten bleiben intern erhalten.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.project_detail", project_id=project_id) + "#abrechnung")


@bp.get("/projects/<project_id>/billing.json")
@login_required
def project_billing_projection(project_id: str):
    try:
        projection = _projects().billing_projection(project_id, str(g.user["username"]))
    except ValueError:
        abort(404)
    # This endpoint is deliberately invoice-safe: internal composition and
    # notes never leave the project page.
    return {"project_id": projection["project_id"], "project_title": projection["project_title"], "lines": projection["lines"]}


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
    theme = request.form.get("theme", "system").strip().casefold()
    if theme not in {"light", "dark", "system"}:
        flash("Unbekanntes Farbschema. / Unknown color scheme.")
        return redirect(url_for("documents.settings"))
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
        get_db().execute("UPDATE user SET display_name = ?, theme = ?, profile_source = ?, profile_updated_at = CURRENT_TIMESTAMP WHERE id = ?", (display_name, theme, "manual", g.user["id"]))
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
    return redirect(url_for("documents.settings"))


@bp.post("/todo")
@login_required
def add_todo():
    try: _todos().add(request.form.get("title", ""), str(g.user["username"]), request.form.to_dict())
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks") if request.form.get("return_to") == "tasks" else url_for("documents.dashboard"))


@bp.post("/todo/<item_id>")
@login_required
def update_todo(item_id: str):
    try: _todos().update(item_id, request.form.to_dict(), str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks") if request.form.get("return_to") == "tasks" else url_for("documents.dashboard") + "#todo")


@bp.post("/todo/<item_id>/toggle")
@login_required
def toggle_todo(item_id: str):
    try: _todos().toggle(item_id, str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks") if request.form.get("return_to") == "tasks" else url_for("documents.dashboard"))


@bp.get("/tasks")
@login_required
def tasks():
    actor = str(g.user["username"]); rows = _todos().items(actor)
    status = request.args.get("status", "").strip(); list_id = request.args.get("list_id", "").strip()
    project_id = request.args.get("project_id", "").strip(); contact_id = request.args.get("contact_id", "").strip()
    category = request.args.get("category", "").strip(); priority = request.args.get("priority", "").strip(); period = request.args.get("period", "").strip()
    if status: rows = [row for row in rows if row.get("status") == status]
    if list_id: rows = [row for row in rows if row.get("list_id") == list_id]
    if project_id: rows = [row for row in rows if row.get("project_id") == project_id]
    if contact_id: rows = [row for row in rows if row.get("contact_id") == contact_id]
    if category: rows = [row for row in rows if category in row.get("categories", [])]
    if priority: rows = [row for row in rows if str(row.get("priority", 0)) == priority]
    today = date.today(); week_end = today + timedelta(days=7)
    if period == "today": rows = [row for row in rows if str(row.get("due", ""))[:10] == today.isoformat()]
    elif period == "week": rows = [row for row in rows if row.get("due") and today.isoformat() <= str(row["due"])[:10] <= week_end.isoformat()]
    elif period == "overdue": rows = [row for row in rows if row.get("due") and str(row["due"])[:10] < today.isoformat() and row.get("status") not in {"completed", "cancelled"}]
    elif period == "none": rows = [row for row in rows if not row.get("due")]
    all_rows = _todos().items(actor)
    document_ids = {str(document_id) for row in rows for document_id in row.get("document_ids", [])}
    document_ids.update(str(row.get("email_document_id")) for row in rows if row.get("email_document_id"))
    task_documents: dict[str, dict[str, Any]] = {}
    for document_id in document_ids:
        try:
            task_documents[document_id] = _store().get_document(document_id)
        except ValueError:
            continue
    task_external_attachments: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        links = []
        for line in row.get("extra_lines", []):
            raw = str(line)
            if not raw.upper().startswith("ATTACH") or ":" not in raw:
                continue
            header, value = raw.split(":", 1)
            value = value.strip()
            if len(value) > 2000 or any(ord(character) < 32 for character in value):
                continue
            try:
                parsed = urlsplit(value)
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            label = next((part.split("=", 1)[1].strip('"') for part in header.split(";") if part.upper().startswith("FILENAME=")), "")
            links.append({"url": value, "label": label or parsed.path.rsplit("/", 1)[-1] or parsed.netloc})
        task_external_attachments[str(row["id"])] = links[:50]
    contacts = _contacts().contacts(actor)
    task_contacts = {str(contact["contact_id"]): contact for contact in contacts}
    return render_template("documents/tasks.html", tasks=rows, task_lists=_todos().lists(actor), projects=_projects().projects(), contacts=contacts,
                           users=[item["username"] for item in get_db().execute("SELECT username FROM user ORDER BY username COLLATE NOCASE").fetchall()],
                           categories=sorted({value for row in all_rows for value in row.get("categories", [])}, key=str.casefold), today=today.isoformat(), view=request.args.get("view", "list"),
                           task_documents=task_documents, task_external_attachments=task_external_attachments, task_contacts=task_contacts)


@bp.post("/tasks/lists")
@login_required
def create_task_list():
    try: _todos().create_list(request.form.to_dict(), str(g.user["username"])); flash("Aufgabenliste angelegt. / Task list created.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks"))


@bp.post("/tasks/lists/<list_id>")
@login_required
def update_task_list(list_id: str):
    permissions: dict[str, list[str]] = {}
    for user in request.form.getlist("shared_users"):
        permissions[user] = [right for right in ("read", "create", "edit", "complete", "delete", "manage") if user in request.form.getlist(right)]
    try:
        _todos().update_list(list_id, {**request.form.to_dict(), "archived": request.form.get("archived") == "1", "permissions": permissions}, str(g.user["username"]))
        flash("Aufgabenliste gespeichert. / Task list saved.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks"))


@bp.post("/tasks/<item_id>/comments")
@login_required
def add_task_comment(item_id: str):
    try: _todos().add_comment(item_id, request.form.get("text", ""), str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks") + "#task-" + item_id)


@bp.post("/tasks/<item_id>/time")
@login_required
def add_task_time(item_id: str):
    try: _todos().book_time(item_id, request.form.get("minutes", ""), request.form.get("note", ""), str(g.user["username"]), request.form.get("date", ""))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks") + "#task-" + item_id)


@bp.post("/tasks/<item_id>/delete")
@login_required
def delete_task(item_id: str):
    try: _todos().soft_delete(item_id, str(g.user["username"])); flash("Aufgabe gelöscht. / Task deleted.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.tasks"))


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


@bp.get("/<document_id>/thumbnail")
@login_required
def document_thumbnail(document_id: str):
    document = _document_or_404(document_id)
    path = PreviewService(_store().root).cached_path(document, "thumbnail")
    if path is None:
        original = _store().root / document.get("last_path", "")
        if not original.is_file() or original.is_symlink():
            abort(404)
        path = original
        max_age = 0
    else:
        max_age = 31536000
    response = send_file(path, conditional=True, etag=True, max_age=max_age)
    response.headers["Cache-Control"] = f"private, max-age={max_age}" + (", immutable" if max_age else ", no-cache")
    return response


@bp.get("/<document_id>/collage")
@login_required
def document_collage(document_id: str):
    document = _document_or_404(document_id)
    path = PreviewService(_store().root).cached_path(document, "collage")
    if path is None:
        abort(404)
    response = send_file(path, conditional=True, etag=True, max_age=31536000)
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@bp.post("/<document_id>/tags")
@login_required
def set_document_tags(document_id: str):
    try: _store().set_tags(document_id, request.form.get("tags", "").split(","), str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.images"))


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
    return redirect(url_for("documents.images"))


