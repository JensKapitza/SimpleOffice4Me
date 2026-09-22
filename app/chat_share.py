"""Safe adapters for sharing existing SimpleOffice objects in chat.

Only compact display snapshots are copied into a chat message. Sensitive contact
fields, internal audit metadata and raw calendar/task payloads stay in their
source stores.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .calendar_store import CalendarStore
from .contact_store import ContactStore
from .document_store import DocumentStore
from .todo_store import TodoStore

SHARE_KINDS = {"contact", "task", "event", "document"}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _contact_card(root: str | Path, object_id: str, actor: str, *, include_vcard: bool = False) -> dict[str, Any]:
    item = ContactStore(root).get(object_id, actor)
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    card = {
        "kind": "contact",
        "object_id": str(item.get("contact_id") or object_id),
        "title": _text(fields.get("display_name") or "Kontakt", 200),
        "subtitle": _text(fields.get("company") or fields.get("title"), 200),
        "email": _text(fields.get("email"), 320),
        "phone": _text(fields.get("phone"), 120),
    }
    if include_vcard:
        card["vcard"] = ContactStore(root).vcard(object_id, actor)
    return card


def _task_card(root: str | Path, object_id: str, actor: str) -> dict[str, Any]:
    item = next((row for row in TodoStore(root).items(actor) if str(row.get("id")) == object_id), None)
    if item is None:
        raise ValueError("Aufgabe nicht gefunden oder nicht freigegeben")
    return {
        "kind": "task",
        "object_id": str(item.get("id") or object_id),
        "title": _text(item.get("title") or "Aufgabe", 240),
        "status": _text(item.get("status"), 40),
        "due": _text(item.get("due"), 80),
        "priority": int(item.get("priority") or 0),
        "done": bool(item.get("done")),
    }


def _event_card(root: str | Path, object_id: str, actor: str) -> dict[str, Any]:
    item = CalendarStore(root).get(object_id, actor)
    return {
        "kind": "event",
        "object_id": str(item.get("event_id") or object_id),
        "title": _text(item.get("title") or "Termin", 240),
        "start": _text(item.get("start"), 80),
        "end": _text(item.get("end"), 80),
        "location": _text(item.get("location") or item.get("place"), 240),
        "status": _text(item.get("status") or "active", 40),
    }


def _document_card(root: str | Path, object_id: str, actor: str) -> dict[str, Any]:
    store = DocumentStore(root)
    item = store.get_document(object_id)
    path = str(item.get("last_path") or "")
    return {
        "kind": "document",
        "object_id": str(item.get("document_id") or object_id),
        "title": _text(Path(path).name or "Dokument", 240),
        "path": _text(path, 500),
        "sha256": _text(item.get("sha256"), 64),
        "tags": [_text(tag, 80) for tag in list(item.get("tags") or [])[:12]],
    }


def build_share_card(root: str | Path, kind: str, object_id: str, actor: str, *, include_vcard: bool = False) -> dict[str, Any]:
    kind = str(kind or "").strip().casefold()
    object_id = str(object_id or "").strip()
    if kind not in SHARE_KINDS or not object_id:
        raise ValueError("Ungültiges Chat-Objekt")
    if kind == "contact":
        return _contact_card(root, object_id, actor, include_vcard=include_vcard)
    if kind == "task":
        return _task_card(root, object_id, actor)
    if kind == "event":
        return _event_card(root, object_id, actor)
    return _document_card(root, object_id, actor)


def share_choices(root: str | Path, actor: str, *, limit: int = 100) -> dict[str, list[dict[str, str]]]:
    limit = max(1, min(int(limit), 250))
    contacts = []
    for item in ContactStore(root).contacts(actor)[:limit]:
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        contacts.append({"id": str(item.get("contact_id") or ""), "label": _text(fields.get("display_name") or "Kontakt", 200)})
    tasks = [
        {"id": str(item.get("id") or ""), "label": _text(item.get("title") or "Aufgabe", 200)}
        for item in TodoStore(root).items(actor)[:limit]
    ]
    events = [
        {"id": str(item.get("event_id") or ""), "label": _text(item.get("title") or "Termin", 200)}
        for item in CalendarStore(root).events(actor)[:limit]
        if item.get("status") not in {"deleted", "cancelled"}
    ]
    documents = []
    for item in DocumentStore(root).list_documents()[:limit]:
        path = str(item.get("last_path") or "")
        documents.append({"id": str(item.get("document_id") or ""), "label": _text(Path(path).name or path or "Dokument", 200)})
    return {"contact": contacts, "task": tasks, "event": events, "document": documents}
