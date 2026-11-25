"""Authorized, bounded mail body extraction for private local reading."""
from __future__ import annotations

import hashlib
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from .document_store import DocumentStore
from .mail_archive_storage import archive_document, archive_path_by_id
from .mail_client import MailStore
from .mail_message_cache import message_cache
from .private_read_aloud import MAX_SPEECH_CHARS, ReadAloudError, read_aloud_request
from .reader_speech_text import verified_speech_source
from .v2.storage_runtime import storage_for


MAX_SPEECH_EML_BYTES = 16 * 1024 * 1024
_IGNORED_HTML_TAGS = {"head", "script", "style", "template", "noscript"}
_BLOCK_HTML_TAGS = {"br", "p", "div", "li", "tr", "th", "td", "hr", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}


class _BodyText(HTMLParser):
    """Flatten message HTML without loading resources or including script/style text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored: list[str] = []
        self._length = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORED_HTML_TAGS:
            self._ignored.append(tag)
        elif tag in _BLOCK_HTML_TAGS:
            self.handle_data("\n")

    def handle_endtag(self, tag):
        if self._ignored and self._ignored[-1] == tag:
            self._ignored.pop()
        elif tag in _BLOCK_HTML_TAGS:
            self.handle_data("\n")

    def handle_data(self, data):
        # Keep an extra character past a possible trailing separator, so a
        # later meaningful character cannot turn overflow into silent clipping.
        limit = MAX_SPEECH_CHARS + 2
        if self._ignored or self._length >= limit:
            return
        text = " ".join(data.split())
        if not text and data and self._parts and not self._parts[-1].endswith(" "):
            text = " "
        elif text and data[:1].isspace() and self._parts and not self._parts[-1].endswith(" "):
            text = " " + text
        if text.strip() and data[-1:].isspace():
            text += " "
        if text:
            bounded = text[:limit - self._length]
            self._parts.append(bounded)
            self._length += len(bounded)

    def text(self) -> str:
        return "".join(self._parts).strip()


def _body_parts(message: EmailMessage):
    # Walking all descendants would also read text from an attached EML or a
    # named multipart/inline attachment. Only traverse the message's own body.
    pending = [message]
    while pending:
        part = pending.pop()
        if part.is_attachment() or part.get_filename():
            continue
        if part.get_content_maintype() == "multipart":
            children = list(part.iter_parts())
            if part.get_content_subtype() == "related" and children:
                start = part.get_param("start")
                children = [next((child for child in children if start and child["Content-ID"] == start), children[0])]
            pending.extend(reversed(children))
        elif part.get_content_type() in {"text/plain", "text/html"}:
            yield part


def mail_body_for_read_aloud(raw: bytes) -> str:
    if len(raw) > MAX_SPEECH_EML_BYTES:
        raise ReadAloudError("too_large", 413)
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        plain: list[str] = []
        html: list[str] = []
        for part in _body_parts(message):
            try:
                text = part.get_content()
            except (LookupError, UnicodeError, ValueError):
                text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            if not isinstance(text, str) or not text.strip():
                continue
            if part.get_content_type() == "text/plain":
                plain.append(text.strip())
            else:
                parser = _BodyText()
                parser.feed(text)
                parser.close()
                if body := parser.text():
                    html.append(body)
        text = " ".join("\n\n".join(plain or html).split())
    except (LookupError, RecursionError, UnicodeError, ValueError, TypeError, IndexError):
        raise ReadAloudError("source_unavailable", 404) from None
    if not text:
        raise ReadAloudError("no_text", 422)
    if len(text) > MAX_SPEECH_CHARS:
        raise ReadAloudError("too_large", 413)
    return text.encode("utf-8", "replace").decode("utf-8")


def requested_mail_version(length: int) -> str:
    page, version = read_aloud_request()
    if page != 1 or not re.fullmatch(rf"[0-9a-f]{{{length}}}", version):
        raise ReadAloudError("invalid_request", 400)
    return version


def live_mail_text(store: MailStore, actor: str, account_id: str, folder: str, uid: str, version: str) -> str:
    # Recheck ownership and do not resolve a secret or connect to IMAP on play.
    try:
        store._owned_row(actor, account_id)
    except (OSError, ValueError, PermissionError, KeyError):
        raise ReadAloudError("source_unavailable", 404) from None
    raw = message_cache().get(actor, account_id, folder, uid)
    if raw is None:
        raise ReadAloudError("document_changed", 409)
    if len(raw) > MAX_SPEECH_EML_BYTES:
        raise ReadAloudError("too_large", 413)
    if hashlib.sha512(raw).hexdigest() != version:
        raise ReadAloudError("document_changed", 409)
    return mail_body_for_read_aloud(raw)


def archive_mail_text(store: MailStore, owner: str, account_id: str, archive_id: str, version: str) -> str:
    try:
        path = archive_path_by_id(store, owner, account_id, archive_id)
        document = archive_document(store, owner, account_id, path, scan_unregistered=False)
        raw = verified_speech_source(
            storage_for(store.root, owner), document, MAX_SPEECH_EML_BYTES, expected_location=path,
        )
    except ReadAloudError:
        raise
    except (OSError, ValueError, PermissionError, KeyError):
        raise ReadAloudError("source_unavailable", 404) from None
    if hashlib.sha512(raw).hexdigest() != archive_id.casefold() or archive_id.casefold() != version:
        raise ReadAloudError("document_changed", 409)
    return mail_body_for_read_aloud(raw)


def document_eml_text(root: Path, actor: str, document_id: str, version: str) -> str:
    try:
        document = DocumentStore(root).get_document(document_id)
        path = Path(str(document.get("last_path") or ""))
        if document["document_id"] != document_id or path.suffix.casefold() != ".eml":
            raise ValueError("not an EML document")
        raw = verified_speech_source(
            storage_for(root, actor), document, MAX_SPEECH_EML_BYTES, expected_location=path.as_posix(),
        )
    except ReadAloudError:
        raise
    except (OSError, ValueError, PermissionError, RuntimeError, KeyError):
        raise ReadAloudError("source_unavailable", 404) from None
    if hashlib.sha256(raw).hexdigest() != version:
        raise ReadAloudError("document_changed", 409)
    return mail_body_for_read_aloud(raw)
