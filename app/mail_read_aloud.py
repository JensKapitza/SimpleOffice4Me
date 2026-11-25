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
MAX_MAIL_SECTIONS_CHARS = 24_000
MAIL_SECTION_CHARS = 1000
MAX_MAIL_SECTIONS = 48
_IGNORED_HTML_TAGS = {"head", "script", "style", "template", "noscript"}
_BLOCK_HTML_TAGS = {"br", "p", "div", "li", "tr", "th", "td", "hr", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}


class _BodyText(HTMLParser):
    """Flatten message HTML without loading resources or including script/style text."""

    def __init__(self, maximum: int) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored: list[str] = []
        self._length = 0
        self.limit = maximum + 2
        self.clipped = False
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
        limit = self.limit
        if self._ignored:
            return
        if self._length >= limit:
            self.clipped = self.clipped or bool(data.strip())
            return
        text = " ".join(data.split())
        if not text and data and self._parts and not self._parts[-1].endswith(" "):
            text = " "
        elif text and data[:1].isspace() and self._parts and not self._parts[-1].endswith(" "):
            text = " " + text
        if text.strip() and data[-1:].isspace():
            text += " "
        if text:
            bound = limit - self._length
            bounded = text[:bound]
            self.clipped = self.clipped or bool(text[bound:].strip())
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


def _parsed_mail(raw: bytes) -> EmailMessage:
    if len(raw) > MAX_SPEECH_EML_BYTES:
        raise ReadAloudError("too_large", 413)
    try:
        return BytesParser(policy=policy.default).parsebytes(raw)
    except (LookupError, RecursionError, UnicodeError, ValueError, TypeError, IndexError):
        raise ReadAloudError("source_unavailable", 404) from None


def _mail_body_text(message: EmailMessage, maximum: int) -> str:
    try:
        plain: list[str] = []
        html: list[str] = []
        html_clipped = False
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
                parser = _BodyText(maximum)
                parser.feed(text)
                parser.close()
                html_clipped = html_clipped or parser.clipped
                if body := parser.text():
                    html.append(body)
        text = " ".join("\n\n".join(plain or html).split())
    except (LookupError, RecursionError, UnicodeError, ValueError, TypeError, IndexError):
        raise ReadAloudError("source_unavailable", 404) from None
    if not text:
        raise ReadAloudError("no_text", 422)
    if len(text) > maximum or (not plain and html_clipped):
        raise ReadAloudError("too_large", 413)
    return text.encode("utf-8", "replace").decode("utf-8")


def mail_body_for_read_aloud(raw: bytes) -> str:
    return _mail_body_text(_parsed_mail(raw), MAX_SPEECH_CHARS)


def _mail_sections(text: str) -> list[str]:
    result = []
    start = 0
    while start < len(text):
        end = min(start + MAIL_SECTION_CHARS, len(text))
        if end < len(text):
            split = text.rfind(" ", start + MAIL_SECTION_CHARS // 2, end + 1)
            if split > start:
                end = min(split + 1, start + MAIL_SECTION_CHARS)
        result.append(text[start:end])
        start = end
    if len(result) > MAX_MAIL_SECTIONS:
        raise ReadAloudError("too_large", 413)
    return result


def preview_mail_speech_section_count(message: EmailMessage, raw_bytes: int) -> int:
    """Compute count from a preview's already authorized MIME object, not another GET/read."""
    if not 0 < raw_bytes <= MAX_SPEECH_EML_BYTES:
        return 0
    try:
        text = _mail_body_text(message, MAX_MAIL_SECTIONS_CHARS)
        return len(_mail_sections(text)) if len(text) > MAIL_SECTION_CHARS else 0
    except ReadAloudError:
        return 0


def _mail_speech_text(raw: bytes, part: int | None) -> str:
    if part is None:
        return mail_body_for_read_aloud(raw)
    text = _mail_body_text(_parsed_mail(raw), MAX_MAIL_SECTIONS_CHARS)
    if len(text) <= MAIL_SECTION_CHARS:
        raise ReadAloudError("invalid_page", 404)
    sections = _mail_sections(text)
    if not 1 <= part <= len(sections):
        raise ReadAloudError("invalid_page", 404)
    return sections[part - 1].strip()


def requested_mail_selection(length: int, *, parts: bool = False) -> tuple[int | None, str]:
    page, version = read_aloud_request()
    if not 1 <= page <= (MAX_MAIL_SECTIONS + 1 if parts else 1) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", version):
        raise ReadAloudError("invalid_request", 400)
    return (page - 1 if parts and page > 1 else None), version


def requested_mail_version(length: int) -> str:
    return requested_mail_selection(length)[1]


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


def _archive_mail_source(store: MailStore, owner: str, account_id: str, archive_id: str, version: str) -> bytes:
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
    return raw


def archive_mail_text(
    store: MailStore, owner: str, account_id: str, archive_id: str, version: str, *, part: int | None = None,
) -> str:
    return _mail_speech_text(_archive_mail_source(store, owner, account_id, archive_id, version), part)


def _document_eml_source(root: Path, actor: str, document_id: str, version: str) -> bytes:
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
    return raw


def document_eml_text(root: Path, actor: str, document_id: str, version: str, *, part: int | None = None) -> str:
    return _mail_speech_text(_document_eml_source(root, actor, document_id, version), part)
