"""Permission-scoped contact audit and management views."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .contact_management import ContactManagement
from .contact_store import ContactStore
from .contact_tools import ContactTools
from .db import get_db


bp = Blueprint("contact_audit", __name__)
PAGE_SIZE = 50
LABELS = {
    "de": {"title": "Änderungshistorie", "description": "Sichtbare Kontaktänderungen nach Benutzer und Feld durchsuchen.", "search": "Suchen", "search_hint": "Kontakt, Wert oder Benutzer", "editor": "Bearbeitet von", "all_editors": "Alle Bearbeiter", "changed_field": "Geändertes Feld", "all_fields": "Alle Felder", "filter": "Filtern", "changes": "Änderungen", "page": "Seite", "reset": "Zurücksetzen", "no_entries": "Keine passenden Kontaktänderungen.", "pagination": "Seitennavigation", "previous": "Zurück", "next": "Weiter"},
    "en": {"title": "Change history", "description": "Search visible contact changes by user and field.", "search": "Search", "search_hint": "Contact, value, or user", "editor": "Edited by", "all_editors": "All editors", "changed_field": "Changed field", "all_fields": "All fields", "filter": "Filter", "changes": "changes", "page": "Page", "reset": "Reset", "no_entries": "No matching contact changes.", "pagination": "Pagination", "previous": "Previous", "next": "Next"},
}


def change_history(store: ContactStore, actor: str, query: str = "", editor: str = "", field: str = "", offset: int = 0, limit: int = PAGE_SIZE) -> dict[str, Any]:
    """Return audit history only for contacts the actor may edit.

    Read-only sharing exposes the current contact, not historical field values
    that may contain data removed before the share was granted.
    """
    if not actor.strip():
        raise ValueError("a named user is required for contact history")
    needle = query.strip().casefold()
    selected_editor = editor.strip()
    selected_field = field.strip()
    entries: list[dict[str, Any]] = []
    editors: set[str] = set()
    fields: set[str] = set()
    for contact in store.contacts(actor):
        contact_id = str(contact.get("contact_id", ""))
        if not store.can_manage_contact(contact, actor):
            continue
        display_name = str(contact.get("fields", {}).get("display_name", ""))
        for change in contact.get("changes", []):
            if not isinstance(change, dict):
                continue
            changed_by = str(change.get("actor", "")).strip()
            changed_field = str(change.get("field", "")).strip()
            if changed_by:
                editors.add(changed_by)
            if changed_field:
                fields.add(changed_field)
            entry = {"contact_id": contact_id, "display_name": display_name, "field": changed_field, "old": change.get("old", ""), "new": change.get("new", ""), "at": str(change.get("at", "")), "actor": changed_by}
            if selected_editor and changed_by != selected_editor:
                continue
            if selected_field and changed_field != selected_field:
                continue
            if needle and needle not in " ".join(str(value) for value in entry.values()).casefold():
                continue
            entries.append(entry)
    entries.sort(key=lambda item: (item["at"], item["contact_id"], item["field"]), reverse=True)
    total = len(entries)
    start = max(0, int(offset))
    page_size = max(1, min(int(limit), 100))
    return {"entries": entries[start:start + page_size], "total": total, "editors": sorted(editors, key=str.casefold), "fields": sorted(fields, key=str.casefold)}


@bp.get("/documents/contacts/history")
@login_required
def history():
    actor = str(g.user["username"])
    query = request.args.get("q", "").strip()
    editor = request.args.get("editor", "").strip()
    field = request.args.get("field", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    store = ContactStore(current_app.config["DOCUMENT_ROOT"])
    result = change_history(store, actor, query, editor, field, (page - 1) * PAGE_SIZE, PAGE_SIZE)
    pages = max(1, (result["total"] + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        page = pages
        result = change_history(store, actor, query, editor, field, (page - 1) * PAGE_SIZE, PAGE_SIZE)
    language = g.language if g.language in LABELS else "de"
    return render_template("documents/contact_history.html", history=result, query=query, editor=editor, field=field, page=page, pages=pages, labels=LABELS[language])


def _management() -> ContactManagement:
    return ContactManagement(current_app.config["DOCUMENT_ROOT"])


def _tools() -> ContactTools:
    return ContactTools(current_app.config["DOCUMENT_ROOT"])


def _actor() -> str:
    return str(g.user["username"])


@bp.get("/documents/contacts/manage")
@login_required
def manage():
    manager = _management()
    visible_contacts = manager.store.contacts(_actor())
    query = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    group = request.args.get("group", "").strip()
    company = request.args.get("company", "").strip()
    incomplete = request.args.get("incomplete", "").strip()
    contacts = manager.advanced_search(_actor(), query, tag, group, company, incomplete, contacts=visible_contacts)
    return render_template(
        "documents/contact_management.html",
        dashboard=manager.dashboard(_actor(), visible_contacts),
        contacts=contacts,
        query=query,
        selected_tag=tag,
        selected_group=group,
        company=company,
        incomplete=incomplete,
    )


@bp.get("/documents/contacts/manage/duplicates")
@login_required
def duplicates():
    return render_template(
        "documents/contact_duplicates.html",
        duplicates=_management().duplicate_candidates(_actor()),
    )


@bp.post("/documents/contacts/<contact_id>/metadata")
@login_required
def update_metadata(contact_id: str):
    manager = _management()
    try:
        manager.update_metadata(contact_id, _actor(), request.form.get("tags", "").split(","), request.form.get("groups", "").split(","))
        flash("Tags und Gruppen gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


@bp.post("/documents/contacts/<contact_id>/access")
@login_required
def update_sharing(contact_id: str):
    actor = _actor()
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    managers = request.form.getlist("managers")
    readers = request.form.getlist("readers")
    unknown = sorted((set(managers) | set(readers)) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        ContactStore(current_app.config["DOCUMENT_ROOT"]).share(contact_id, managers, actor, readers)
        flash("Kontaktfreigaben gespeichert. Lesen und Bearbeiten sind getrennt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


@bp.post("/documents/contacts/bulk-metadata")
@login_required
def bulk_metadata():
    try:
        changed = _management().bulk_metadata(request.form.getlist("contact_ids"), _actor(), request.form.get("add_tags", "").split(","), request.form.get("add_groups", "").split(","))
        flash(f"{changed} Kontakt(e) aktualisiert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("contact_audit.manage"))


@bp.post("/documents/contacts/bulk-export")
@login_required
def bulk_export():
    try:
        payload = _tools().export_selected(request.form.getlist("contact_ids"), _actor())
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("contact_audit.manage"))
    response = Response(payload, content_type="text/vcard; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="simpleoffice-contacts.vcf"'
    return response


@bp.post("/documents/contacts/merge")
@login_required
def merge_contacts():
    target_id = request.form.get("target_id", "").strip()
    source_id = request.form.get("source_id", "").strip()
    try:
        merged = _management().merge(target_id, source_id, _actor())
        flash("Kontakte revisionssicher zusammengeführt. Beide vorherigen Fassungen wurden gesichert.")
        if request.form.get("return_to") == "duplicates":
            return redirect(url_for("contact_audit.duplicates"))
        return redirect(url_for("documents.contact_detail", contact_id=merged["contact_id"]))
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("contact_audit.duplicates" if request.form.get("return_to") == "duplicates" else "contact_audit.manage"))


@bp.get("/documents/contacts/<contact_id>/snapshots")
@login_required
def snapshots(contact_id: str):
    manager = _management()
    if not manager.store.can_manage(contact_id, _actor()):
        abort(403)
    contact = manager.store.get(contact_id, _actor())
    return render_template("documents/contact_snapshots.html", contact=contact, snapshots=manager.snapshots(contact_id, _actor()))


@bp.get("/documents/contacts/<contact_id>/snapshots/<snapshot_id>/compare")
@login_required
def compare_snapshot(contact_id: str, snapshot_id: str):
    manager = _management()
    if not manager.store.can_manage(contact_id, _actor()):
        abort(403)
    try:
        comparison = _tools().compare_snapshot(contact_id, snapshot_id, _actor())
        return render_template("documents/contact_snapshot_compare.html", comparison=comparison)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("contact_audit.snapshots", contact_id=contact_id))


@bp.post("/documents/contacts/restore/<snapshot_id>")
@login_required
def restore_snapshot(snapshot_id: str):
    try:
        contact = _management().restore(snapshot_id, _actor())
        flash("Kontaktversion wiederhergestellt. Die vorherige aktuelle Version wurde ebenfalls gesichert.")
        return redirect(url_for("documents.contact_detail", contact_id=contact["contact_id"]))
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("contact_audit.manage"))


def _uploaded_csv() -> str:
    upload = request.files.get("contacts_file")
    if upload is None:
        raise ValueError("Keine Importdatei ausgewählt.")
    raw = upload.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("CSV import is limited to 2 MiB")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV muss UTF-8 kodiert sein") from exc


@bp.post("/documents/contacts/import-preview")
@login_required
def import_preview():
    try:
        preview = _tools().preview_csv(_uploaded_csv(), _actor())
        return render_template("documents/contact_import_preview.html", preview=preview, filename=request.files["contacts_file"].filename or "Import")
    except ValueError as exc:
        flash(f"Importvorschau fehlgeschlagen: {exc}")
        return redirect(url_for("contact_audit.manage"))


@bp.post("/documents/contacts/import-csv")
@login_required
def import_csv():
    try:
        result = _tools().import_csv(_uploaded_csv(), _actor())
        flash(f"CSV-Import abgeschlossen: {result['created']} neu, {result['skipped_duplicates']} vorhandene E-Mail-Dublette(n) übersprungen.")
    except ValueError as exc:
        flash(f"CSV-Import abgebrochen: {exc}")
    return redirect(url_for("contact_audit.manage"))


from .contact_extensions import register as _register_contact_extensions
_register_contact_extensions(bp)

# Business-document routes are nested into this already registered blueprint so
# the application keeps one contact-related registration point.
from .business_documents import bp as _business_documents_bp
bp.register_blueprint(_business_documents_bp)
