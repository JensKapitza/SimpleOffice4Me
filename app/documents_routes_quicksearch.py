"""Central quick-search workspace: live JSON API plus shared match collection.

All entity searches reuse the existing stores and their own permission rules,
so the API never exposes more than the corresponding detail pages.
"""
from __future__ import annotations

import json
from typing import Any

from flask import current_app, g, jsonify, request, url_for

from .auth import login_required
from .documents_core import _calendar, _contacts, _objects, _projects, _store, bp


KIND_ORDER = (
    "document", "appointment", "task", "chat", "contact",
    "project", "object", "invoice", "link",
)
PER_KIND_LIMIT = 12
TOTAL_LIMIT = 50
MAX_QUERY_LENGTH = 200
MIN_API_QUERY_LENGTH = 2
CHAT_ROOMS_SCANNED = 10
CHAT_MESSAGES_SCANNED = 300


def relevance_score(needle: str, label: str, detail: str = "") -> int:
    """Explainable relevance: exact beats prefix beats substring beats detail."""
    label_folded = (label or "").casefold()
    if needle == label_folded:
        return 100
    if label_folded.startswith(needle):
        return 80
    if needle in label_folded:
        return 60
    if detail and needle in detail.casefold():
        return 40
    return 20


def _like_escape(text: str) -> str:
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _actor() -> str:
    return str(g.user["username"])


def _document_matches(query: str) -> list[dict[str, Any]]:
    try:
        literal = json.dumps(query + "*", ensure_ascii=False)
        rows = _store().search_page(literal, page_size=25)["results"]
    except ValueError:
        return []
    needle = query.casefold()
    matches = []
    for row in rows:
        label = row["path"]
        matches.append({
            "kind": "document", "label": label, "detail": row.get("state", ""),
            "url": url_for("documents.detail", document_id=row["document_id"]),
            "ref": row["document_id"],
            "score": relevance_score(needle, label, row.get("state", "")),
        })
    return matches


def _object_matches(query: str) -> list[dict[str, Any]]:
    needle = query.casefold()
    matches = []
    for row in _objects().objects(query)[:PER_KIND_LIMIT]:
        label = row.get("name", row["object_id"])
        detail = row.get("display_id", "")
        matches.append({
            "kind": "object", "label": label, "detail": detail,
            "url": url_for("documents.object_detail", object_id=row["object_id"]),
            "ref": row["object_id"],
            "score": relevance_score(needle, label, detail),
        })
    return matches


def _project_matches(query: str) -> list[dict[str, Any]]:
    needle = query.casefold()
    matches = []
    for row in _projects().projects():
        if needle not in json.dumps(row, ensure_ascii=False).casefold():
            continue
        label = row.get("title", row["project_id"])
        detail = row.get("status", "")
        matches.append({
            "kind": "project", "label": label, "detail": detail,
            "url": url_for("documents.project_detail", project_id=row["project_id"]),
            "ref": row["project_id"],
            "score": relevance_score(needle, label, detail),
        })
        if len(matches) >= PER_KIND_LIMIT:
            break
    return matches


def _contact_matches(query: str, actor: str) -> list[dict[str, Any]]:
    needle = query.casefold()
    matches = []
    for row in _contacts().search(query, actor)[:PER_KIND_LIMIT]:
        fields = row.get("fields", {})
        label = fields.get("display_name", row["contact_id"])
        detail = fields.get("company", "")
        matches.append({
            "kind": "contact", "label": label, "detail": detail,
            "url": url_for("documents.contact_detail", contact_id=row["contact_id"]),
            "ref": row["contact_id"],
            "score": relevance_score(needle, label, detail),
        })
    return matches


def _invoice_matches(query: str, actor: str) -> list[dict[str, Any]]:
    from .business_documents import invoices

    needle = query.casefold()
    contacts = _contacts()
    matches = []
    for row in invoices(_store().root):
        searchable = " ".join((
            str(row.get("invoice_number", "")),
            str(row.get("buyer", {}).get("name", "")),
        )).casefold()
        if needle not in searchable or not contacts.can_manage(row.get("contact_id", ""), actor):
            continue
        label = row.get("invoice_number", row["invoice_id"])
        detail = row.get("buyer", {}).get("name", "")
        matches.append({
            "kind": "invoice", "label": label, "detail": detail,
            "url": url_for("contact_audit.business_documents.customer_billing", contact_id=row["contact_id"]),
            "ref": row["invoice_id"],
            "score": relevance_score(needle, label, detail),
        })
        if len(matches) >= PER_KIND_LIMIT:
            break
    return matches


def _appointment_matches(query: str, actor: str) -> list[dict[str, Any]]:
    needle = query.casefold()
    matches = []
    for event in _calendar().events(actor):
        if event.get("status", "active") in {"cancelled", "deleted", "moved"}:
            continue
        title = str(event.get("title", ""))
        reason = str(event.get("reason", ""))
        if needle not in f"{title} {reason}".casefold():
            continue
        start = str(event.get("start", ""))
        matches.append({
            "kind": "appointment", "label": title or start, "detail": start,
            "url": url_for("documents.calendar", month=start[:7]) if len(start) >= 7 else url_for("documents.calendar"),
            "ref": event.get("event_id", ""),
            "score": relevance_score(needle, title, reason),
        })
        if len(matches) >= PER_KIND_LIMIT:
            break
    return matches


def _task_matches(query: str, actor: str) -> list[dict[str, Any]]:
    from .todo_store import TodoStore

    needle = query.casefold()
    store = TodoStore(current_app.config["DOCUMENT_ROOT"])
    matches = []
    for row in store.items(actor):
        haystack = " ".join([
            str(row.get("title", "")), str(row.get("description", "")),
            " ".join(row.get("categories", [])),
        ]).casefold()
        if needle not in haystack:
            continue
        label = row.get("title", row.get("id", ""))
        detail = str(row.get("due", ""))
        matches.append({
            "kind": "task", "label": label, "detail": detail,
            "url": url_for("tasks.board") + f"#task-{row.get('id', '')}",
            "ref": row.get("id", ""),
            "score": relevance_score(needle, label, detail),
        })
        if len(matches) >= PER_KIND_LIMIT:
            break
    return matches


def _chat_matches(query: str, actor: str) -> list[dict[str, Any]]:
    from .chat_store import ChatStore

    needle = query.casefold()
    is_admin = bool(g.user["is_admin"] and not g.user["is_disabled"])
    store = ChatStore(current_app.config["DOCUMENT_ROOT"])
    matches = []
    rooms = store.rooms_for(actor, is_admin=is_admin)
    for room in rooms:
        title = str(room.get("title", ""))
        if needle in title.casefold():
            matches.append({
                "kind": "chat", "label": title, "detail": str(room.get("updated_at", "")),
                "url": url_for("chat.room", room_id=room["room_id"]),
                "ref": room["room_id"],
                "score": relevance_score(needle, title),
            })
    for room in rooms[:CHAT_ROOMS_SCANNED]:
        try:
            messages = store.messages(room["room_id"], limit=CHAT_MESSAGES_SCANNED)
        except (ValueError, PermissionError):
            continue
        for message in messages:
            body = str(message.get("body", ""))
            if not body or needle not in body.casefold():
                continue
            snippet = body if len(body) <= 100 else body[:97] + "..."
            matches.append({
                "kind": "chat", "label": snippet,
                "detail": str(room.get("title", "")),
                "url": url_for("chat.room", room_id=room["room_id"]) + f"#message-{message.get('message_id', '')}",
                "ref": f"{room['room_id']}/{message.get('message_id', '')}",
                "score": 30,
            })
            if len(matches) >= PER_KIND_LIMIT * 2:
                break
        if len(matches) >= PER_KIND_LIMIT * 2:
            break
    return matches[:PER_KIND_LIMIT]


def _link_matches(query: str) -> list[dict[str, Any]]:
    from . import db as database

    needle = query.casefold()
    pattern = f"%{_like_escape(query.strip())}%"
    user_id = int(g.user["id"])
    _ensure_bookmark_schema = None
    try:
        from .browser_bookmarks import _ensure_schema

        _ensure_bookmark_schema = _ensure_schema
    except ImportError:
        return []
    _ensure_bookmark_schema()
    rows = database.get_db().execute(
        "SELECT id, title, url, folder FROM browser_bookmark WHERE user_id=? AND ("
        "title LIKE ? ESCAPE '!' OR url LIKE ? ESCAPE '!' OR "
        "folder LIKE ? ESCAPE '!' OR tags LIKE ? ESCAPE '!') "
        "ORDER BY pinned DESC, title COLLATE NOCASE LIMIT ?",
        (user_id, pattern, pattern, pattern, pattern, PER_KIND_LIMIT),
    ).fetchall()
    matches = []
    for row in rows:
        label = row["title"]
        detail = row["folder"] or row["url"]
        matches.append({
            "kind": "link", "label": label, "detail": detail,
            "url": row["url"], "ref": row["id"],
            "score": relevance_score(needle, label, detail),
        })
    return matches


def collect_quick_matches(query: str, *, kinds: list[str] | None = None) -> list[dict[str, Any]]:
    """Collect permission-checked matches across registers, sorted by relevance."""
    query = (query or "").strip()
    if not query or len(query) > MAX_QUERY_LENGTH:
        return []
    wanted = [kind for kind in (kinds or list(KIND_ORDER)) if kind in KIND_ORDER]
    actor = _actor()
    collectors = {
        "document": lambda: _document_matches(query),
        "object": lambda: _object_matches(query),
        "project": lambda: _project_matches(query),
        "contact": lambda: _contact_matches(query, actor),
        "invoice": lambda: _invoice_matches(query, actor),
        "appointment": lambda: _appointment_matches(query, actor),
        "task": lambda: _task_matches(query, actor),
        "chat": lambda: _chat_matches(query, actor),
        "link": lambda: _link_matches(query),
    }
    matches: list[dict[str, Any]] = []
    for kind in wanted:
        matches.extend(sorted(
            collectors[kind](), key=lambda item: (-item["score"], item["label"].casefold()),
        )[:PER_KIND_LIMIT])
    order = {kind: position for position, kind in enumerate(KIND_ORDER)}
    matches.sort(key=lambda item: (-item["score"], order[item["kind"]], item["label"].casefold()))
    return matches[:TOTAL_LIMIT]


def preview_for(kind: str, ref: str) -> dict[str, Any] | None:
    """Small permission-checked preview rows; nothing beyond detail-page data."""
    actor = _actor()
    if kind == "document":
        try:
            from .document_store import DocumentStore

            document = DocumentStore(current_app.config["DOCUMENT_ROOT"]).get_document(ref)
        except (ValueError, OSError, KeyError):
            return None
        return {"title": str(document.get("last_path", "")), "rows": [
            ("Status", str(document.get("state", ""))),
            ("Aktualisiert", str(document.get("last_seen_at", ""))),
        ], "url": url_for("documents.detail", document_id=ref)}
    if kind == "appointment":
        try:
            event = _calendar().get(ref, actor)
        except ValueError:
            return None
        return {"title": str(event.get("title", "")), "rows": [
            ("Zeit", str(event.get("start", ""))),
            ("Beschreibung", str(event.get("reason", ""))[:200]),
        ], "url": url_for("documents.calendar", month=str(event.get("start", ""))[:7])}
    if kind == "task":
        from .todo_store import TodoStore

        row = next((item for item in TodoStore(current_app.config["DOCUMENT_ROOT"]).items(actor)
                    if item.get("id") == ref), None)
        if row is None:
            return None
        return {"title": str(row.get("title", "")), "rows": [
            ("Fällig", str(row.get("due", ""))),
            ("Beschreibung", str(row.get("description", ""))[:200]),
        ], "url": url_for("tasks.board") + f"#task-{ref}"}
    if kind == "chat":
        from .chat_store import ChatStore

        room_id = ref.split("/", 1)[0]
        store = ChatStore(current_app.config["DOCUMENT_ROOT"])
        is_admin = bool(g.user["is_admin"] and not g.user["is_disabled"])
        if not store.can_access(room_id, actor, is_admin=is_admin):
            return None
        try:
            room = store.room(room_id)
        except ValueError:
            return None
        return {"title": str(room.get("title", "")), "rows": [
            ("Aktualisiert", str(room.get("updated_at", ""))),
        ], "url": url_for("chat.room", room_id=room_id)}
    if kind == "contact":
        try:
            row = _contacts().get(ref, actor)
        except (ValueError, KeyError):
            return None
        fields = row.get("fields", {})
        return {"title": str(fields.get("display_name", ref)), "rows": [
            ("Firma", str(fields.get("company", ""))),
        ], "url": url_for("documents.contact_detail", contact_id=ref)}
    if kind == "project":
        try:
            row = _projects().project(ref)
        except (ValueError, KeyError):
            return None
        return {"title": str(row.get("title", ref)), "rows": [
            ("Status", str(row.get("status", ""))),
        ], "url": url_for("documents.project_detail", project_id=ref)}
    if kind == "object":
        try:
            row = _objects().object(ref)
        except (ValueError, KeyError):
            return None
        return {"title": str(row.get("name", ref)), "rows": [
            ("Details", str(row.get("display_id", ""))),
        ], "url": url_for("documents.object_detail", object_id=ref)}
    if kind == "link":
        from . import db as database

        row = database.get_db().execute(
            "SELECT title, url, folder FROM browser_bookmark WHERE id=? AND user_id=?",
            (ref, int(g.user["id"])),
        ).fetchone()
        if row is None:
            return None
        return {"title": str(row["title"]), "rows": [
            ("Adresse", str(row["url"])),
            ("Ordner", str(row["folder"] or "")),
        ], "url": str(row["url"])}
    return None


@bp.get("/quick-search/api")
@login_required
def quick_search_api():
    query = request.args.get("q", "").strip()
    if len(query) > MAX_QUERY_LENGTH:
        return jsonify({"error": "Suchbegriff zu lang."}), 400
    if len(query) < MIN_API_QUERY_LENGTH:
        return jsonify({"matches": [], "query": query})
    kinds = [kind.strip() for kind in request.args.getlist("type")] or None
    if kinds is not None:
        kinds = [kind for kind in kinds if kind in KIND_ORDER] or None
        if request.args.getlist("type") and not kinds:
            return jsonify({"matches": [], "query": query})
    matches = collect_quick_matches(query, kinds=kinds)
    return jsonify({"matches": [
        {"kind": item["kind"], "label": item["label"], "detail": item["detail"],
         "url": item["url"], "ref": item["ref"], "score": item["score"]}
        for item in matches
    ], "query": query})


@bp.get("/quick-search/preview")
@login_required
def quick_search_preview():
    kind = (request.args.get("kind", "") or "").strip()
    ref = (request.args.get("id", "") or "").strip()
    if not kind and not ref:
        return jsonify({"kind": "", "title": "", "rows": [], "url": ""})
    if kind not in KIND_ORDER or not ref or len(ref) > 128:
        return jsonify({"error": "Unbekannter Eintrag."}), 404
    preview = preview_for(kind, ref)
    if preview is None:
        return jsonify({"error": "Unbekannter Eintrag."}), 404
    return jsonify({"kind": kind, **preview})
