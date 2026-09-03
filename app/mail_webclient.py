"""Readonly-first IMAP webclient helpers.

The existing mail stack is intentionally reused for TLS/authentication.  This
module adds mailbox browsing and narrowly-scoped mutations without changing the
archive format.  Every account is read-only unless an explicit per-user policy
allows writes.
"""
from __future__ import annotations

import base64
import hashlib
import imaplib
import json
import re
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .contact_store import ContactStore
from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .mail_client import ImapArchive, MailStore, MAX_MESSAGE_BYTES

MAX_LIST_MESSAGES = 100
MAX_SEARCH_MESSAGES = 500
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
_UID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_LIST_RE = re.compile(rb"^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|\".*?\")\s+(?P<name>.+)$")


class MailReadOnlyError(PermissionError):
    """Raised when a server-mutating action is attempted for a read-only account."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


def _header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        return str(value)


def _decode_modified_utf7(value: bytes | str) -> str:
    text = value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
    result: list[str] = []
    index = 0
    while index < len(text):
        amp = text.find("&", index)
        if amp < 0:
            result.append(text[index:])
            break
        result.append(text[index:amp])
        end = text.find("-", amp)
        if end < 0:
            result.append(text[amp:])
            break
        token = text[amp + 1:end]
        if not token:
            result.append("&")
        else:
            token = token.replace(",", "/")
            token += "=" * ((4 - len(token) % 4) % 4)
            try:
                result.append(base64.b64decode(token).decode("utf-16-be"))
            except (ValueError, UnicodeError):
                result.append(text[amp:end + 1])
        index = end + 1
    return "".join(result)


def _encode_modified_utf7(value: str) -> str:
    if not value or "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("invalid IMAP folder")
    if len(value) > 500:
        raise ValueError("IMAP folder name is too long")
    result: list[str] = []
    non_ascii: list[str] = []

    def flush() -> None:
        if not non_ascii:
            return
        raw = "".join(non_ascii).encode("utf-16-be")
        encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        result.append("&" + encoded + "-")
        non_ascii.clear()

    for char in value:
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            flush()
            result.append("&-" if char == "&" else char)
        else:
            non_ascii.append(char)
    flush()
    return "".join(result)


def _uid(value: str | int) -> str:
    value = str(value)
    if not _UID_RE.fullmatch(value):
        raise ValueError("invalid message UID")
    return value


def _flag_names(metadata: bytes | str) -> set[str]:
    raw = metadata if isinstance(metadata, bytes) else str(metadata).encode("utf-8", "replace")
    return {
        flag.decode("ascii", "replace") if isinstance(flag, bytes) else str(flag)
        for flag in imaplib.ParseFlags(raw)
    }


def _message_literal(response: Any) -> bytes:
    return ImapArchive._literal(response)


class MailAccountPolicy:
    """Per-user write policy. Missing rows deliberately mean read-only."""

    def __init__(self, store: MailStore):
        self.store = store
        self.path = store.control / "account-policies.json"
        self.lock = self.path.with_suffix(".lock")

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"version": 1, "accounts": {}}
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "accounts": {}}

    @staticmethod
    def _key(actor: str, account_id: str) -> str:
        return hashlib.sha256(f"{actor}\0{account_id}".encode("utf-8")).hexdigest()

    def read_only(self, actor: str, account_id: str) -> bool:
        row = self._read().get("accounts", {}).get(self._key(actor, account_id), {})
        return bool(row.get("read_only", True))

    def set_read_only(self, actor: str, account_id: str, value: bool) -> bool:
        # Ownership check without persisting or validating the supplied placeholder.
        self.store.account(actor, account_id, password="ownership-check")
        with exclusive_file_lock(self.lock):
            payload = self._read()
            payload.setdefault("version", 1)
            payload.setdefault("accounts", {})[self._key(actor, account_id)] = {
                "account_id": account_id,
                "owner": actor,
                "read_only": bool(value),
                "updated_at": utc_now(),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_write(self.path, payload)
        self.store.history.record(
            "mail_account_readonly_changed",
            actor,
            "mail-accounts",
            account_id,
            {"read_only": bool(value), "updated_at": utc_now()},
        )
        return bool(value)

    def require_writable(self, actor: str, account_id: str) -> None:
        if self.read_only(actor, account_id):
            raise MailReadOnlyError(
                "Dieses Mailkonto ist schreibgeschützt. Schreibzugriff muss in den Kontoeinstellungen ausdrücklich freigegeben werden."
            )


class ImapWebClient:
    def __init__(self, store: MailStore):
        self.store = store
        self.account_policy = MailAccountPolicy(store)

    def _connect(self, account: dict[str, Any]):
        # Reuse the existing hardened TLS/authentication implementation.
        return ImapArchive(self.store)._connect(account)

    @staticmethod
    def _folder_role(name: str, flags: set[str]) -> str:
        lowered = name.casefold()
        normalized = {flag.casefold() for flag in flags}
        for marker, role in (
            ("\\inbox", "inbox"), ("\\sent", "sent"), ("\\drafts", "drafts"),
            ("\\trash", "trash"), ("\\junk", "junk"), ("\\archive", "archive"),
        ):
            if marker in normalized:
                return role
        if name.upper() == "INBOX":
            return "inbox"
        for token, role in (
            ("sent", "sent"), ("gesendet", "sent"), ("draft", "drafts"), ("entwurf", "drafts"),
            ("trash", "trash"), ("papierkorb", "trash"), ("junk", "junk"), ("spam", "junk"),
            ("archive", "archive"), ("archiv", "archive"),
        ):
            if token in lowered:
                return role
        return "folder"

    def folders(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        connection = self._connect(account)
        try:
            status, rows = connection.list()
            if status != "OK":
                raise RuntimeError("IMAP LIST failed")
            result: list[dict[str, Any]] = []
            for raw in rows or []:
                raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("ascii", "replace")
                match = _LIST_RE.match(raw_bytes)
                if not match:
                    continue
                flags = {
                    token.decode("ascii", "replace")
                    for token in match.group("flags").split()
                }
                if "\\Noselect" in flags:
                    selectable = False
                else:
                    selectable = True
                delimiter_raw = match.group("delimiter")
                delimiter = "" if delimiter_raw == b"NIL" else delimiter_raw.strip(b'"').decode("ascii", "replace")
                name = _decode_modified_utf7(match.group("name"))
                result.append({
                    "name": name,
                    "delimiter": delimiter,
                    "flags": sorted(flags),
                    "selectable": selectable,
                    "role": self._folder_role(name, flags),
                })
            order = {"inbox": 0, "drafts": 1, "sent": 2, "archive": 3, "junk": 4, "trash": 5, "folder": 6}
            return sorted(result, key=lambda row: (order.get(row["role"], 9), row["name"].casefold()))
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    @staticmethod
    def _select(connection: Any, folder: str, *, readonly: bool) -> int:
        status, data = connection.select(_encode_modified_utf7(folder), readonly=readonly)
        if status != "OK":
            raise RuntimeError("IMAP folder could not be selected")
        try:
            return int((data or [b"0"])[0] or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _summary(connection: Any, uid: str) -> dict[str, Any] | None:
        status, response = connection.uid(
            "fetch", uid,
            "(FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO CC DATE MESSAGE-ID)])",
        )
        if status != "OK":
            return None
        header_bytes = _message_literal(response)
        if not header_bytes:
            return None
        parsed = BytesParser(policy=policy.default).parsebytes(header_bytes, headersonly=True)
        metadata = b""
        for item in response or []:
            if isinstance(item, tuple) and item and isinstance(item[0], bytes):
                metadata = item[0]
                break
        size_match = re.search(rb"RFC822\.SIZE\s+(\d+)", metadata)
        flags = _flag_names(metadata)
        return {
            "uid": uid,
            "subject": _header(parsed.get("Subject")) or "(ohne Betreff)",
            "from": _header(parsed.get("From")),
            "to": _header(parsed.get("To")),
            "cc": _header(parsed.get("Cc")),
            "date": _header(parsed.get("Date")),
            "message_id": str(parsed.get("Message-ID") or ""),
            "size": int(size_match.group(1)) if size_match else 0,
            "flags": sorted(flags),
            "seen": "\\Seen" in flags,
            "flagged": "\\Flagged" in flags,
            "answered": "\\Answered" in flags,
        }

    def messages(
        self,
        account: dict[str, Any],
        folder: str,
        *,
        page: int = 1,
        per_page: int = 50,
        query: str = "",
    ) -> dict[str, Any]:
        page = max(1, int(page))
        per_page = max(10, min(int(per_page), MAX_LIST_MESSAGES))
        connection = self._connect(account)
        try:
            count = self._select(connection, folder, readonly=True)
            status, data = connection.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("IMAP SEARCH failed")
            uids = [token.decode("ascii", "strict") for token in ((data or [b""])[0] or b"").split()]
            uids.reverse()
            needle = query.strip().casefold()
            if needle:
                candidates = uids[:MAX_SEARCH_MESSAGES]
                filtered: list[dict[str, Any]] = []
                for uid in candidates:
                    summary = self._summary(connection, _uid(uid))
                    if not summary:
                        continue
                    haystack = " ".join(
                        str(summary.get(key, "")) for key in ("subject", "from", "to", "cc", "date")
                    ).casefold()
                    if needle in haystack:
                        filtered.append(summary)
                total = len(filtered)
                start = (page - 1) * per_page
                rows = filtered[start:start + per_page]
            else:
                total = len(uids)
                start = (page - 1) * per_page
                rows = [
                    summary for uid in uids[start:start + per_page]
                    if (summary := self._summary(connection, _uid(uid))) is not None
                ]
            return {
                "folder": folder,
                "messages": rows,
                "page": page,
                "per_page": per_page,
                "total": total,
                "mailbox_count": count,
                "has_prev": page > 1,
                "has_next": page * per_page < total,
                "query": query.strip(),
            }
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    @staticmethod
    def _body_text(message: Any) -> str:
        candidates: list[str] = []
        html_candidates: list[str] = []
        parts = message.walk() if message.is_multipart() else [message]
        for part in parts:
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                value = part.get_content()
            except (LookupError, UnicodeError):
                raw = part.get_payload(decode=True) or b""
                value = raw.decode(part.get_content_charset() or "utf-8", "replace")
            if not isinstance(value, str):
                continue
            if content_type == "text/plain":
                candidates.append(value)
            else:
                html_candidates.append(value)
        if candidates:
            return "\n\n".join(candidates).strip()
        if html_candidates:
            parser = _TextExtractor()
            parser.feed("\n".join(html_candidates))
            return parser.text().strip()
        return ""

    @staticmethod
    def _attachments(message: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for part in message.walk():
            filename = _header(part.get_filename())
            if not filename and part.get_content_disposition() != "attachment":
                continue
            raw = part.get_payload(decode=True) or b""
            result.append({
                "index": len(result),
                "filename": filename or f"attachment-{len(result) + 1}",
                "content_type": part.get_content_type(),
                "size": len(raw),
            })
        return result

    def _raw_message(self, account: dict[str, Any], folder: str, uid: str) -> tuple[bytes, set[str]]:
        connection = self._connect(account)
        try:
            self._select(connection, folder, readonly=True)
            status, response = connection.uid("fetch", _uid(uid), "(FLAGS RFC822.SIZE BODY.PEEK[])")
            if status != "OK":
                raise KeyError("message not found")
            raw = _message_literal(response)
            if not raw:
                raise KeyError("message not found")
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("message exceeds maximum supported size")
            metadata = b""
            for item in response or []:
                if isinstance(item, tuple) and item and isinstance(item[0], bytes):
                    metadata = item[0]
                    break
            return raw, _flag_names(metadata)
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    def message(self, account: dict[str, Any], folder: str, uid: str) -> dict[str, Any]:
        raw, flags = self._raw_message(account, folder, uid)
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        return {
            "uid": _uid(uid),
            "subject": _header(parsed.get("Subject")) or "(ohne Betreff)",
            "from": _header(parsed.get("From")),
            "to": _header(parsed.get("To")),
            "cc": _header(parsed.get("Cc")),
            "date": _header(parsed.get("Date")),
            "message_id": str(parsed.get("Message-ID") or ""),
            "flags": sorted(flags),
            "seen": "\\Seen" in flags,
            "body_text": self._body_text(parsed),
            "attachments": self._attachments(parsed),
            "size": len(raw),
        }

    def attachment(self, account: dict[str, Any], folder: str, uid: str, index: int) -> dict[str, Any]:
        raw, _ = self._raw_message(account, folder, uid)
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        attachments = []
        for part in parsed.walk():
            filename = _header(part.get_filename())
            if not filename and part.get_content_disposition() != "attachment":
                continue
            data = part.get_payload(decode=True) or b""
            attachments.append((filename or f"attachment-{len(attachments) + 1}", part.get_content_type(), data))
        if index < 0 or index >= len(attachments):
            raise KeyError("attachment not found")
        filename, content_type, data = attachments[index]
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError("attachment exceeds 50 MiB web download limit")
        filename = re.sub(r"[\r\n\\/]+", "_", filename).strip()[:240] or "attachment.bin"
        return {"filename": filename, "content_type": content_type, "data": data}

    def create_folder(self, actor: str, account: dict[str, Any], name: str) -> str:
        self.account_policy.require_writable(actor, account["id"])
        encoded = _encode_modified_utf7(name.strip())
        connection = self._connect(account)
        try:
            status, _ = connection.create(encoded)
            if status != "OK":
                raise RuntimeError("IMAP CREATE failed")
        finally:
            try:
                connection.logout()
            except Exception:
                pass
        self.store.history.record(
            "imap_folder_created", actor, "mail-accounts", account["id"], {"folder": name.strip(), "at": utc_now()}
        )
        return name.strip()

    def set_seen(self, actor: str, account: dict[str, Any], folder: str, uid: str, seen: bool) -> None:
        self.account_policy.require_writable(actor, account["id"])
        connection = self._connect(account)
        try:
            self._select(connection, folder, readonly=False)
            command = "+FLAGS.SILENT" if seen else "-FLAGS.SILENT"
            status, _ = connection.uid("store", _uid(uid), command, "(\\Seen)")
            if status != "OK":
                raise RuntimeError("IMAP STORE failed")
        finally:
            try:
                connection.logout()
            except Exception:
                pass
        self.store.history.record(
            "imap_message_seen_changed", actor, "mail-accounts", account["id"],
            {"folder": folder, "uid": _uid(uid), "seen": bool(seen), "at": utc_now()},
        )

    def move(self, actor: str, account: dict[str, Any], folder: str, uid: str, target: str) -> None:
        self.account_policy.require_writable(actor, account["id"])
        if folder == target:
            raise ValueError("source and target folder are identical")
        connection = self._connect(account)
        try:
            self._select(connection, folder, readonly=False)
            capabilities = {
                item.decode("ascii", "replace").upper() if isinstance(item, bytes) else str(item).upper()
                for item in getattr(connection, "capabilities", ())
            }
            uid_value = _uid(uid)
            target_encoded = _encode_modified_utf7(target)
            if "MOVE" in capabilities:
                status, _ = connection.uid("MOVE", uid_value, target_encoded)
                if status != "OK":
                    raise RuntimeError("IMAP MOVE failed")
            elif "UIDPLUS" in capabilities:
                status, _ = connection.uid("COPY", uid_value, target_encoded)
                if status != "OK":
                    raise RuntimeError("IMAP COPY failed")
                status, _ = connection.uid("STORE", uid_value, "+FLAGS.SILENT", "(\\Deleted)")
                if status != "OK":
                    raise RuntimeError("IMAP delete flag failed")
                status, _ = connection.uid("EXPUNGE", uid_value)
                if status != "OK":
                    raise RuntimeError("IMAP UID EXPUNGE failed")
            else:
                raise RuntimeError("Server unterstützt weder MOVE noch UIDPLUS; sichere Verschiebung nicht möglich")
        finally:
            try:
                connection.logout()
            except Exception:
                pass
        self.store.history.record(
            "imap_message_moved", actor, "mail-accounts", account["id"],
            {"source": folder, "target": target, "uid": _uid(uid), "at": utc_now()},
        )


def contact_recipients(root: str | Path, actor: str, query: str = "", limit: int = 100) -> list[dict[str, str]]:
    store = ContactStore(root)
    contacts = store.search(query, actor) if query.strip() else store.contacts(actor)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for contact in contacts:
        fields = contact.get("fields", {})
        label = str(fields.get("display_name") or fields.get("company") or "").strip()
        raw_values: list[str] = []
        email_value = fields.get("email", "")
        if isinstance(email_value, list):
            raw_values.extend(str(value) for value in email_value)
        else:
            raw_values.append(str(email_value))
        for _, address in getaddresses(raw_values):
            normalized = address.strip().casefold()
            if not normalized or normalized in seen or "@" not in normalized:
                continue
            seen.add(normalized)
            result.append({
                "email": address.strip(),
                "label": label or address.strip(),
                "contact_id": str(contact.get("contact_id") or ""),
            })
            if len(result) >= max(1, min(int(limit), 250)):
                return result
    return result
