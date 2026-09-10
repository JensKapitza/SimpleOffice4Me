"""Document HTTP route group extracted from app.documents."""
from __future__ import annotations

from .documents_core import *  # noqa: F401,F403

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
    security = _attachment_security()
    safe_attachments = _released_eml_attachments(store, document)
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
        preview={**_preview_data(document), "url": url_for("documents.image_preview", document_id=document_id), "thumbnail_url": url_for("documents.document_thumbnail", document_id=document_id), "collage_url": url_for("documents.document_collage", document_id=document_id) if document.get("preview", {}).get("collage") else "", "preview_status": document.get("preview", {}).get("status", "pending"), "name": document.get("last_path", "").rsplit("/", 1)[-1], "text": (document.get("extracted_text") or document.get("ocr_text") or "")[:12000]},
        defaults=_settings().settings(),
        document_tasks=[row for row in _todos().items(str(g.user["username"])) if document_id in row.get("document_ids", [])],
        malware_scan=security.latest_document_scan(document),
        safe_attachments=safe_attachments,
    )


@bp.post("/<document_id>/tasks")
@login_required
def create_document_task(document_id: str):
    document = _document_or_404(document_id)
    try:
        title = request.form.get("title", "") or ("Prüfen: " + document.get("last_path", "Dokument"))
        description = request.form.get("description", "").strip() or (
            "Dokument prüfen, erforderliche Bearbeitung durchführen und Ergebnis in der Aufgabe dokumentieren: "
            + str(document.get("last_path", "Dokument"))
        )
        _todos().add(title, str(g.user["username"]), {**request.form.to_dict(), "description": description, "document_ids": [document_id]})
        flash("Aufgabe mit dem Dokument verknüpft. / Task linked to document.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


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
            safe_attachments = _released_eml_attachments(_store(), document)
            return render_template("documents/attachments.html", document=document, manifest=manifest, safe_attachments=safe_attachments)
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


@bp.post("/<document_id>/security/scan")
@login_required
def security_scan_document(document_id: str):
    _document_or_404(document_id)
    try:
        record = _attachment_security().scan_document(document_id, str(g.user["username"]))
        if record["verdict"] == "clean":
            flash("ClamAV: The document is clean." if g.language == "en" else "ClamAV: Das Dokument ist unauffällig.")
        elif record["verdict"] == "infected":
            flash("ClamAV: Malware detected. The document was reported and not modified." if g.language == "en" else "ClamAV: Schadsoftware erkannt. Das Dokument wurde gemeldet und nicht verändert.")
        else:
            flash(("ClamAV scan failed: " if g.language == "en" else "ClamAV-Prüfung fehlgeschlagen: ") + record.get("detail", ""))
    except (OSError, RuntimeError, ValueError) as exc:
        flash(("ClamAV scan failed: " if g.language == "en" else "ClamAV-Prüfung fehlgeschlagen: ") + str(exc))
    view = request.form.get("return_view", "detail")
    if view == "search":
        return redirect(url_for("documents.document_search", q=request.form.get("q", ""), page=request.form.get("page", "1")))
    if view == "index":
        return redirect(url_for("documents.index", page=request.form.get("page", "1")))
    return redirect(url_for("documents.detail", document_id=document_id))


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
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    page_size = 50
    store = _contacts()
    visible_contacts = store.contacts(actor)
    matches = store.search(query, actor, contacts=visible_contacts)
    total = len(matches)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    start = (page - 1) * page_size
    contacts = matches[start:start + page_size]
    address_values = sorted({address.get("value", "") for contact in contacts for address in contact.get("addresses", []) if address.get("value")}, key=str.casefold)
    carddav_endpoint = url_for("carddav.endpoint", path=f"addressbooks/{g.user['username']}/default/", _external=True)
    company_contacts = [{"contact_id": item["contact_id"], "name": store.company_name(item)} for item in visible_contacts if store.company_name(item)]
    return render_template(
        "documents/contacts.html", contacts=contacts, query=query,
        schema=store.schema(), carddav=store.carddav(), carddav_endpoint=carddav_endpoint,
        address_matches=store.address_matches(contacts), address_values=address_values,
        page=page, pages=pages, total=total, company_contacts=company_contacts,
    )


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
    store = _contacts(); visible = store.contacts(actor); company_id = str(contact.get("fields", {}).get("company_contact_id", ""))
    linked_company = next((item for item in visible if item.get("contact_id") == company_id), None)
    company_contacts = [{"contact_id": item["contact_id"], "name": store.company_name(item)} for item in visible if item.get("contact_id") != contact_id and store.company_name(item)]
    return render_template("documents/contact_detail.html", contact=contact, users=users, has_photo=store.has_photo(contact), is_owner=not contact.get("owner") or contact.get("owner") == actor,
                           contact_tasks=_todos().items(actor, contact_id=contact_id), task_lists=_todos().lists(actor), company_contacts=company_contacts,
                           company_people=store.company_people(contact, actor), linked_company=linked_company)


@bp.get("/contacts/company-search")
@login_required
def company_contact_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"items": [], "provider": "local_contacts"})
    actor = str(g.user["username"]); store = _contacts(); excluded = request.args.get("exclude", "").strip()
    rows = []
    for contact in store.search(query, actor):
        if contact.get("contact_id") == excluded: continue
        fields = contact.get("fields", {}); company_name = store.company_name(contact)
        if not company_name: continue
        rows.append({
            "source": "contact",
            "contact_id": contact["contact_id"], "company_name": company_name,
            "display_name": str(fields.get("display_name", "")), "email": str(fields.get("email", "")),
        })
        if len(rows) >= 10: break
    # Company names never leave this server. External providers such as Google
    # Places are deliberately not part of the contact search contract.
    return jsonify({"items": rows, "provider": "local_contacts"})


@bp.get("/contacts/<contact_id>/photo")
@login_required
def contact_photo(contact_id: str):
    try:
        payload, media_type = _contacts().photo(contact_id, str(g.user["username"]))
    except ValueError:
        abort(404)
    return Response(payload, mimetype=media_type, headers={"Content-Security-Policy": "default-src 'none'", "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})


@bp.post("/contacts")
@login_required
def save_contact():
    contact_id = request.form.get("contact_id", "")
    try:
        store = _contacts(); actor = str(g.user["username"]); values = request.form.to_dict(); company_id = values.get("company_contact_id", "").strip()
        candidates = store.contacts(actor); company_contact = None
        if company_id:
            if company_id == contact_id: raise ValueError("Ein Kontakt kann nicht sich selbst als Firma zugeordnet werden")
            company_contact = next((item for item in candidates if item.get("contact_id") == company_id), None)
            if company_contact is None: raise ValueError("Die ausgewählte Firma ist nicht verfügbar")
            values["company"] = store.company_name(company_contact); values["custom_company_contact_id"] = company_id
        else:
            typed = str(values.get("company", "")).strip().casefold()
            exact = [item for item in candidates if item.get("contact_id") != contact_id and store.company_name(item).casefold() == typed] if typed else []
            if len(exact) == 1:
                company_contact = exact[0]; values["company"] = store.company_name(company_contact); values["custom_company_contact_id"] = company_contact["contact_id"]
        contact = store.upsert(values, actor, contact_id)
        if company_contact and not contact.get("addresses") and company_contact.get("addresses"):
            source_address = company_contact["addresses"][0]
            store.add_address(contact["contact_id"], "Firma", str(source_address.get("value", "")), actor, components=source_address.get("components", {}))
            contact = store.get(contact["contact_id"], actor)
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
        components = {key: request.form.get(key, "") for key in ("street", "city", "state", "postal", "country")}
        _contacts().add_address(contact_id, request.form.get("label", ""), request.form.get("address", ""), str(g.user["username"]), components)
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
    visible_events = _calendar().events(actor)
    deleted_events = sorted(
        (event for event in visible_events if event.get("status") == "deleted"),
        key=lambda event: event.get("status_changed_at") or event.get("updated_at") or "",
        reverse=True,
    )
    events = [event for event in visible_events if event.get("status", "active") not in {"cancelled", "deleted", "moved"}]
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
    contacts = _contacts().contacts(actor)
    contact_map = {contact["contact_id"]: contact for contact in contacts}
    for event in events:
        contact = contact_map.get(event.get("contact_id"), {})
        fields = contact.get("fields", {})
        contact_name = str(fields.get("display_name") or "").strip()
        event["invite_recipient"] = str(fields.get("email") or "").strip()
        event["invite_search_label"] = " · ".join(
            value for value in (str(event.get("title") or "Termin"), contact_name, str(event.get("start") or "")) if value
        )
    label_counts = Counter(event["invite_search_label"] for event in events)
    for event in events:
        if label_counts[event["invite_search_label"]] > 1:
            event["invite_search_label"] += f" · #{str(event.get('event_id') or '')[:8]}"
    return render_template("documents/calendar.html", events=events, deleted_events=deleted_events, calendars=calendars, contacts=contacts, users=users, current_username=actor, current_user_email=str(g.user["email"] or ""), mail_accounts=_mail().accounts(actor), local_calendar_address=local_calendar_address(actor), scheduling_access=_scheduling_access().get(actor), google_sync=_google_calendar().status(actor), booking=_calendar().booking_settings(), booking_url=url_for("documents.book_calendar_slot", _external=True), pending=_calendar().pending_bookings(), itip_messages=_itip().messages(actor), reminders=reminders, reminder_now=reminder_now.isoformat(timespec="seconds"), defaults=_settings().settings(), calendar_weeks=monthcalendar(shown_month.year, shown_month.month), calendar_events=events_by_day, shown_month=shown_month.strftime("%Y-%m"), shown_month_name=f"{month_name[shown_month.month]} {shown_month.year}", previous_month=previous, following_month=following)


