"""Read-only IMAP mailbox browser and explicit local archiving helpers.

The browser never changes remote flags, moves or deletes messages.  Every
mailbox is opened read-only and message bodies are fetched with BODY.PEEK[].
Archiving writes an unchanged EML into the existing private SimpleOffice mail
archive.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from .document_store import DocumentStore
from .mail_client import ImapArchive, MailStore, MAX_MESSAGE_BYTES, _owner_key


MAX_BROWSER_MESSAGES = 200
MAX_PREVIEW_TEXT = 250_000


def _header(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))[:1000]
    except Exception:
        return str(value)[:1000]


def _folder_name(raw: bytes | str) -> str:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    match = re.search(r"\)\s+(?:\"[^\"]*\"|NIL)\s+(.+)$", text)
    value = match.group(1).strip() if match else text.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
    return value[:500]


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    return html.unescape(value)


def _message_text(message) -> str:
    plain: list[str] = []
    fallback: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type().lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            text = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if not isinstance(text, str):
            continue
        if content_type == "text/plain":
            plain.append(text)
        else:
            fallback.append(_html_to_text(text))
    result = "\n\n".join(plain or fallback)
    return result[:MAX_PREVIEW_TEXT]


class MailReader:
    def __init__(self, store: MailStore):
        self.store = store
        self.imap = ImapArchive(store)

    def folders(self, account: dict[str, Any]) -> list[str]:
        connection = self.imap._connect(account)
        try:
            status, rows = connection.list()
            if status != "OK":
                raise RuntimeError("IMAP LIST failed")
            result = [_folder_name(row) for row in rows or []]
            return sorted({name for name in result if name}, key=str.casefold)
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    def messages(self, account: dict[str, Any], folder: str, *, limit: int = 50, query: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_BROWSER_MESSAGES))
        connection = self.imap._connect(account)
        try:
            status, _ = connection.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError("IMAP EXAMINE failed")
            status, data = connection.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("IMAP UID SEARCH failed")
            uids = list(reversed((data[0].split() if data and data[0] else [])))
            rows: list[dict[str, Any]] = []
            needle = query.strip().casefold()
            fetch_spec = "(UID RFC822.SIZE BODY.PEEK[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)])"
            # Fetch a bounded window before local filtering.  This keeps a UI
            # search responsive without issuing provider-specific IMAP SEARCH syntax.
            for uid in uids[: max(limit * 5, limit)]:
                status, fetched = connection.uid("fetch", uid, fetch_spec)
                if status != "OK":
                    continue
                try:
                    raw_header = self.imap._literal(fetched)
                except RuntimeError:
                    continue
                message = BytesParser(policy=policy.default).parsebytes(raw_header, headersonly=True)
                row = {
                    "uid": uid.decode("ascii", "replace"),
                    "subject": _header(message.get("Subject")) or "(ohne Betreff)",
                    "from": _header(message.get("From")),
                    "to": _header(message.get("To")),
                    "date": _header(message.get("Date")),
                    "message_id": _header(message.get("Message-ID")),
                    "size": self._fetch_size(fetched),
                }
                if needle and needle not in " ".join(str(value) for value in row.values()).casefold():
                    continue
                rows.append(row)
                if len(rows) >= limit:
                    break
            return rows
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    @staticmethod
    def _fetch_size(fetched: Any) -> int:
        for item in fetched or []:
            head = item[0] if isinstance(item, tuple) and item else item
            if isinstance(head, bytes):
                match = re.search(rb"RFC822\.SIZE\s+(\d+)", head)
                if match:
                    return int(match.group(1))
        return 0

    def preview(self, account: dict[str, Any], folder: str, uid: str) -> dict[str, Any]:
        if not uid.isdigit():
            raise ValueError("invalid IMAP UID")
        connection = self.imap._connect(account)
        try:
            status, _ = connection.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError("IMAP EXAMINE failed")
            status, fetched = connection.uid("fetch", uid, "(UID RFC822.SIZE BODY.PEEK[])")
            if status != "OK":
                raise RuntimeError("IMAP UID FETCH failed")
            raw = self.imap._literal(fetched)
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("message exceeds 100 MiB limit")
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
                "uid": uid,
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
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    def archive_uid(self, actor: str, account: dict[str, Any], folder: str, uid: str) -> dict[str, Any]:
        if not uid.isdigit():
            raise ValueError("invalid IMAP UID")
        connection = self.imap._connect(account)
        try:
            status, _ = connection.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError("IMAP EXAMINE failed")
            raw_validity = connection.untagged_responses.get("UIDVALIDITY", [b""])[0]
            uidvalidity = raw_validity.decode("ascii", "replace") if isinstance(raw_validity, bytes) else str(raw_validity)
            status, fetched = connection.uid("fetch", uid, "(UID RFC822.SIZE BODY.PEEK[])")
            if status != "OK":
                raise RuntimeError("IMAP UID FETCH failed")
            raw = self.imap._literal(fetched)
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("message exceeds 100 MiB archive limit")
            digest = hashlib.sha512(raw).hexdigest()
            self.store.ensure_private_archive(actor, account["id"])
            year = datetime.now(timezone.utc).strftime("%Y")
            path = f"email/{_owner_key(actor)}/{account['id']}/{year}/{digest}.eml"
            documents = DocumentStore(self.store.root)
            target = self.store.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            documents.ensure_folder_policy(target.parent, actor)
            if target.is_file() and not target.is_symlink() and hashlib.sha512(target.read_bytes()).hexdigest() == digest:
                document = documents.get_document(path)
                duplicate = True
            else:
                document = documents.create_document_at(path, raw, actor, max_bytes=MAX_MESSAGE_BYTES)
                documents.set_tags(document["document_id"], ["email", "source:imap", f"imap-account:{account['id']}", f"imap-folder:{folder[:120]}"], actor)
                duplicate = False
            message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
            origin = {
                "account_id": account["id"], "folder": folder, "uidvalidity": uidvalidity, "uid": uid,
                "sha512": digest, "message_id": _header(message.get("Message-ID")),
                "subject": _header(message.get("Subject")), "from": _header(message.get("From")),
                "date": _header(message.get("Date")),
            }
            documents.set_attribute(document["document_id"], "email_origin", origin, actor)
            self.store.history.record("imap_message_archived_manual", actor, "mail-archive", document["document_id"], {**origin, "duplicate": duplicate})
            return {"document_id": document["document_id"], "path": path, "duplicate": duplicate}
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    def local_archive(self, actor: str, account_id: str, *, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        account_id = str(account_id)
        # Ownership check without requiring a real password.
        self.store._owned_row(actor, account_id)
        base = self.store.root / "email" / _owner_key(actor) / account_id
        if not base.is_dir():
            return []
        needle = query.strip().casefold()
        rows: list[dict[str, Any]] = []
        for path in sorted(base.rglob("*.eml"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                raw = path.read_bytes()
                message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
            except OSError:
                continue
            row = {
                "path": str(path.relative_to(self.store.root)),
                "subject": _header(message.get("Subject")) or "(ohne Betreff)",
                "from": _header(message.get("From")),
                "to": _header(message.get("To")),
                "date": _header(message.get("Date")),
                "size": len(raw),
            }
            if needle and needle not in " ".join(str(value) for value in row.values()).casefold():
                continue
            rows.append(row)
            if len(rows) >= max(1, min(int(limit), 500)):
                break
        return rows
