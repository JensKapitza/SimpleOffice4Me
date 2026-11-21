"""Mail/Anhang als Aufgabe oder Termin überführen (Issue #1136, slice 5).

Reuses the existing stacks instead of building a parallel architecture:
live mail metadata comes from :class:`ImapWebClient`, archived mail from
the local EML preview, tasks go to :class:`TodoStore` (surfaced as CalDAV
VTODO) and follow-ups to :class:`CalendarStore`.

Stable references only (Message-ID, SHA-512, account/folder/UID or
archive id, attachment metadata). No message or attachment bytes are
copied into tasks or events.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .calendar_store import CalendarStore
from .mail_archive_preview import load_local_eml_by_id
from .mail_client import MailStore
from .mail_webclient import ImapWebClient
from .todo_store import TodoStore


MAX_NOTE_CHARS = 2000
MAX_TITLE_CHARS = 300


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def normalize_attachments(rows: Any) -> list[dict[str, Any]]:
    """Unify live (index/filename/…) and archived (part/name/…) attachment metadata."""
    result: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index", row.get("part", -1)))
        except (TypeError, ValueError):
            continue
        try:
            size = max(0, int(row.get("size", 0) or 0))
        except (TypeError, ValueError):
            size = 0
        result.append({
            "index": index,
            "name": _clean(row.get("filename", row.get("name")), 240) or f"attachment-{index}",
            "content_type": _clean(row.get("content_type", row.get("type")), 120) or "application/octet-stream",
            "size": size,
            "sha256": _clean(row.get("sha256"), 64),
        })
    return sorted(result, key=lambda item: item["index"])


def live_source(store: MailStore, actor: str, account_id: str, folder: str, uid: str) -> dict[str, Any]:
    """Load read-only live mail metadata; account ownership is enforced by the store."""
    if not folder.strip():
        raise ValueError("mail folder is required")
    account = store.account(actor, account_id.strip())
    message = ImapWebClient(store).message(account, folder.strip(), uid.strip())
    return {
        "kind": "live",
        "account_id": str(account.get("id", account_id)),
        "folder": folder.strip(),
        "uid": str(message["uid"]),
        "archive_id": "",
        "subject": _clean(message.get("subject"), 500) or "(ohne Betreff)",
        "sender": _clean(message.get("from"), 500),
        "date": _clean(message.get("date"), 200),
        "message_id": _clean(message.get("message_id"), 1000),
        "sha512": _clean(message.get("sha512"), 128),
        "attachments": normalize_attachments(message.get("attachments")),
    }


def archive_source(store: MailStore, actor: str, account_id: str, archive_id: str) -> dict[str, Any]:
    """Load archived mail metadata; archive permissions are enforced by the loader."""
    preview = load_local_eml_by_id(store, actor, account_id.strip(), archive_id.strip().casefold())
    return {
        "kind": "archive",
        "account_id": account_id.strip(),
        "folder": "",
        "uid": "",
        "archive_id": _clean(preview.get("sha512"), 128).casefold(),
        "subject": _clean(preview.get("subject"), 500) or "(ohne Betreff)",
        "sender": _clean(preview.get("from"), 500),
        "date": _clean(preview.get("date"), 200),
        "message_id": _clean(preview.get("message_id"), 1000),
        "sha512": _clean(preview.get("sha512"), 128),
        "attachments": normalize_attachments(preview.get("attachments")),
    }


def attachment_ref(source: dict[str, Any], raw_index: str) -> dict[str, Any]:
    """Resolve an attachment by index without touching payload bytes."""
    try:
        wanted = int(str(raw_index or "").strip())
    except (TypeError, ValueError):
        raise ValueError("invalid attachment reference") from None
    for attachment in source.get("attachments", []):
        if attachment.get("index") == wanted:
            return attachment
    raise KeyError("attachment does not exist")


def _source_lines(source: dict[str, Any], attachment: dict[str, Any] | None) -> list[str]:
    lines = [
        "Mail-Referenz (stabile Verknüpfung, keine Kopie):",
        f"- Betreff: {source.get('subject', '')}",
        f"- Von: {source.get('sender', '')}",
        f"- Datum: {source.get('date', '')}",
        f"- Message-ID: {source.get('message_id', '') or '—'}",
        f"- SHA-512: {source.get('sha512', '')}",
    ]
    if source.get("kind") == "live":
        lines.append(
            f"- Quelle: Konto {source.get('account_id', '')}, Ordner {source.get('folder', '')}, UID {source.get('uid', '')}"
        )
    else:
        lines.append(f"- Archiv: Konto {source.get('account_id', '')}, ID {source.get('archive_id', '')}")
    if attachment is not None:
        detail = f"- Anhang: {attachment['name']} ({attachment['content_type']}, {attachment['size']} Bytes"
        if attachment.get("sha256"):
            detail += f", SHA-256 {attachment['sha256']}"
        lines.append(detail + ")")
    return lines


def _validated_note(note: str) -> str:
    text = str(note or "").strip()
    if len(text) > MAX_NOTE_CHARS:
        raise ValueError("note exceeds 2000 characters")
    return text


def _validated_title(title: str) -> str:
    text = str(title or "").strip()
    if not text:
        raise ValueError("title is required")
    return text[:MAX_TITLE_CHARS]


def _validated_due(due: str) -> str:
    text = str(due or "").strip()
    if not text:
        return ""
    try:
        datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("invalid due date") from None
    return text[:100]


def create_task(
    store: MailStore,
    actor: str,
    source: dict[str, Any],
    *,
    title: str,
    note: str = "",
    due: str = "",
    list_id: str = "",
    attachment_index: str = "",
) -> dict[str, Any]:
    """Create a Todo from mail metadata; raises PermissionError without list rights."""
    clean_title = _validated_title(title)
    clean_note = _validated_note(note)
    clean_due = _validated_due(due)
    clean_list = str(list_id or "").strip()
    attachment = attachment_ref(source, attachment_index) if str(attachment_index or "").strip() else None
    todos = TodoStore(store.root)
    if not todos.can_create(actor, {"list_id": clean_list} if clean_list else None):
        raise PermissionError("task list is not writable for this user")
    lines = _source_lines(source, attachment)
    if clean_note:
        lines += ["", f"Notiz: {clean_note}"]
    values: dict[str, Any] = {"description": "\n".join(lines)[:10000], "due": clean_due}
    if clean_list:
        values["list_id"] = clean_list
    item = todos.add(clean_title, actor, values)
    store.history.record(
        "mail_handover_task_created", actor, "todo", item["id"],
        {"message_id": source.get("message_id", ""), "sha512": source.get("sha512", ""),
         "kind": source.get("kind", ""), "due": clean_due},
    )
    return item


def create_followup_event(
    store: MailStore,
    actor: str,
    source: dict[str, Any],
    *,
    title: str,
    start: str,
    end: str = "",
    note: str = "",
    attachment_index: str = "",
) -> dict[str, Any]:
    """Create a calendar follow-up/termin from mail metadata (no payload copies)."""
    clean_title = _validated_title(title)
    clean_note = _validated_note(note)
    clean_start = str(start or "").strip()
    clean_end = str(end or "").strip()
    if not clean_start:
        raise ValueError("start is required")
    try:
        datetime.fromisoformat(clean_start)
    except ValueError:
        raise ValueError("invalid start date") from None
    if clean_end:
        try:
            datetime.fromisoformat(clean_end)
        except ValueError:
            raise ValueError("invalid end date") from None
    attachment = attachment_ref(source, attachment_index) if str(attachment_index or "").strip() else None
    lines = _source_lines(source, attachment)
    if clean_note:
        lines += ["", f"Notiz: {clean_note}"]
    event = CalendarStore(store.root).add(clean_title, "\n".join(lines)[:2000], clean_start, clean_end, "", actor)
    store.history.record(
        "mail_handover_event_created", actor, "calendar", event["event_id"],
        {"message_id": source.get("message_id", ""), "sha512": source.get("sha512", ""),
         "kind": source.get("kind", ""), "start": clean_start},
    )
    return event
