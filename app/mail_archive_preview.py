"""Safe preview helpers for EML files in a user's private mail archive."""

from __future__ import annotations

import hashlib
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from .mail_client import MailStore, MAX_MESSAGE_BYTES, _owner_key
from .mail_reader import _header, _message_text


def load_local_eml(store: MailStore, actor: str, account_id: str, relative_path: str) -> dict[str, Any]:
    """Load one owned archive EML without allowing path traversal or symlink escape."""
    store._owned_row(actor, account_id)
    base = (store.root / "email" / _owner_key(actor) / account_id).resolve()
    requested = Path(relative_path)
    if requested.is_absolute() or ".." in requested.parts or requested.suffix.lower() != ".eml":
        raise ValueError("invalid archive path")

    target = (store.root / requested).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise PermissionError("archive path is outside the owned mail archive") from None
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError("archive message does not exist")

    raw = target.read_bytes()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds 100 MiB preview limit")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    attachments: list[dict[str, Any]] = []
    for part in message.walk():
        if part.get_content_disposition() != "attachment" and not part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append({
            "name": _header(part.get_filename()) or "Anhang",
            "type": part.get_content_type()[:120],
            "size": len(payload),
        })
    return {
        "path": str(target.relative_to(store.root)),
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
    }
