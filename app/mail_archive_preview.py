"""Safe preview helpers for EML files in a user's private mail archive."""

from __future__ import annotations

import hashlib
from email import policy
from email.parser import BytesParser
from typing import Any

from .mail_client import MailStore, MAX_MESSAGE_BYTES
from .mail_reader import _header, _message_text
from .mail_archive_storage import archive_path_by_id, read_archive_eml
from .safe_paths import relative_under


def _preview_from_target(store: MailStore, actor: str, account_id: str, path: str) -> dict[str, Any]:
    raw = read_archive_eml(store, actor, account_id, path)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    attachments: list[dict[str, Any]] = []
    for index, part in enumerate(message.walk()):
        if part.get_content_disposition() != "attachment" and not part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append({
            "part": index,
            "name": _header(part.get_filename()) or f"Anhang-{index}",
            "type": part.get_content_type()[:120],
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    return {
        "path": relative_under(store.root, path, require_name=True).as_posix(),
        "sha512": hashlib.sha512(raw).hexdigest(),
        "subject": _header(message.get("Subject")) or "(ohne Betreff)",
        "from": _header(message.get("From")),
        "to": _header(message.get("To")),
        "cc": _header(message.get("Cc")),
        "date": _header(message.get("Date")),
        "message_id": _header(message.get("Message-ID")),
        "in_reply_to": _header(message.get("In-Reply-To")),
        "references": str(message.get("References") or "").split()[:100],
        "text": _message_text(message),
        "attachments": attachments,
        "size": len(raw),
    }


def preview_eml_bytes(raw: bytes) -> dict[str, Any]:
    """Parse a bounded remote EML into escaped-template-ready preview fields."""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds 100 MiB preview limit")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    attachments: list[dict[str, Any]] = []
    for index, part in enumerate(message.walk()):
        if part.get_content_disposition() != "attachment" and not part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append({
            "part": index,
            "name": _header(part.get_filename()) or f"Anhang-{index}",
            "type": part.get_content_type()[:120],
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    return {
        "sha512": hashlib.sha512(raw).hexdigest(),
        "subject": _header(message.get("Subject")) or "(ohne Betreff)",
        "from": _header(message.get("From")),
        "to": _header(message.get("To")),
        "cc": _header(message.get("Cc")),
        "date": _header(message.get("Date")),
        "message_id": _header(message.get("Message-ID")),
        "text": _message_text(message),
        "attachments": attachments,
        "size": len(raw),
        "remote": True,
    }


def load_local_eml(store: MailStore, actor: str, account_id: str, relative_path: str) -> dict[str, Any]:
    """Load one owned archive EML without allowing path traversal or symlink escape."""
    return _preview_from_target(store, actor, account_id, relative_path)


def load_local_eml_by_id(store: MailStore, actor: str, account_id: str, archive_id: str) -> dict[str, Any]:
    """Load an archived message by its SHA-512 filename instead of a client supplied path."""
    return _preview_from_target(store, actor, account_id, archive_path_by_id(store, actor, account_id, archive_id))


def load_local_attachment_by_id(
    store: MailStore,
    actor: str,
    account_id: str,
    archive_id: str,
    part_index: int,
) -> dict[str, Any]:
    """Return one attachment payload from an owned archived EML by stable identifiers."""
    if part_index < 0 or part_index > 10000:
        raise ValueError("invalid attachment part")
    path = archive_path_by_id(store, actor, account_id, archive_id)
    raw = read_archive_eml(store, actor, account_id, path)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    parts = list(message.walk())
    if part_index >= len(parts):
        raise FileNotFoundError("attachment does not exist")
    part = parts[part_index]
    if part.get_content_disposition() != "attachment" and not part.get_filename():
        raise FileNotFoundError("MIME part is not an attachment")
    payload = part.get_payload(decode=True) or b""
    return {
        "part": part_index,
        "name": _header(part.get_filename()) or f"Anhang-{part_index}",
        "type": part.get_content_type()[:120] or "application/octet-stream",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "payload": payload,
    }
