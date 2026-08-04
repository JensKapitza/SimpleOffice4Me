"""Read-only, permission-scoped contact change history."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, g, render_template, request

from .auth import login_required
from .contact_store import ContactStore


bp = Blueprint("contact_audit", __name__)
PAGE_SIZE = 50
LABELS = {
    "de": {"title": "Änderungshistorie", "description": "Sichtbare Kontaktänderungen nach Benutzer und Feld durchsuchen.", "search": "Suchen", "search_hint": "Kontakt, Wert oder Benutzer", "editor": "Bearbeitet von", "all_editors": "Alle Bearbeiter", "changed_field": "Geändertes Feld", "all_fields": "Alle Felder", "filter": "Filtern", "changes": "Änderungen", "page": "Seite", "reset": "Zurücksetzen", "no_entries": "Keine passenden Kontaktänderungen.", "pagination": "Seitennavigation", "previous": "Zurück", "next": "Weiter"},
    "en": {"title": "Change history", "description": "Search visible contact changes by user and field.", "search": "Search", "search_hint": "Contact, value, or user", "editor": "Edited by", "all_editors": "All editors", "changed_field": "Changed field", "all_fields": "All fields", "filter": "Filter", "changes": "changes", "page": "Page", "reset": "Reset", "no_entries": "No matching contact changes.", "pagination": "Pagination", "previous": "Previous", "next": "Next"},
}


def change_history(store: ContactStore, actor: str, query: str = "", editor: str = "", field: str = "", offset: int = 0, limit: int = PAGE_SIZE) -> dict[str, Any]:
    """Return a bounded, newest-first audit view of contacts visible to actor."""
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
