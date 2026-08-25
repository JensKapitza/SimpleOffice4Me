"""Safe preview helpers for EML files in a user's private mail archive."""

from __future__ import annotations

import hashlib
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from .mail_client import MailStore, MAX_MESSAGE_BYTES, _owner_key
from .mail_reader import _header, _message_text


_ARCHIVE_ID = re.compile(r"^[0-9a-f]{128}$")


def _owned_archive_base(store: MailStore, actor: str, account_id: str) -> Path:
    store._owned_row(actor, account_id)
    return (store.root / "email" / _owner_key(actor) / account_id).resolve()


def _target_by_id(store: MailStore, actor: str, account_id: str, archive_id: str) -> Path:
    archive_id = archive_id.strip().casefold()
    if not _ARCHIVE_ID.fullmatch(archive_id):
        raise ValueError("invalid archive message id")
    base = _owned_archive_base(store, actor, account_id)
    if not base.is_dir():
        raise FileNotFoundError("mail archive does not exist")

    matches: list[Path] = []
    for target in base.glob(f"*/{archive_id}.eml"):
        if target.is_file() and not target.is_symlink():
            matches.append(target.resolve())
            if len(matches) > 1:
                break
    if not matches:
        direct = base / f"{archive_id}.eml"
        if direct.is_file() and not direct.is_symlink():
            matches.append(direct.resolve())
    if len(matches) != 1:
        raise FileNotFoundError("archive message does not exist or is ambiguous")
    try:
        matches[0].relative_to(base)
    except ValueError:
        raise PermissionError("archive message escaped owned archive") from None
    return matches[0]


def _preview_from_target(store: MailStore, target: Path) -> dict[str, Any]:
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError("archive message does not exist")

    raw = target.read_bytes()
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


def load_local_eml(store: MailStore, actor: str, account_id: str, relative_path: str) -> dict[str, Any]:
    """Load one owned archive EML without allowing path traversal or symlink escape."""
    base = _owned_archive_base(store, actor, account_id)
    requested = Path(relative_path)
    if requested.is_absolute() or ".." in requested.parts or requested.suffix.lower() != ".eml":
        raise ValueError("invalid archive path")

    target = (store.root / requested).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise PermissionError("archive path is outside the owned mail archive") from None
    return _preview_from_target(store, target)


def load_local_eml_by_id(store: MailStore, actor: str, account_id: str, archive_id: str) -> dict[str, Any]:
    """Load an archived message by its SHA-512 filename instead of a client supplied path."""
    return _preview_from_target(store, _target_by_id(store, actor, account_id, archive_id))


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
    target = _target_by_id(store, actor, account_id, archive_id)
    raw = target.read_bytes()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds 100 MiB preview limit")
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
