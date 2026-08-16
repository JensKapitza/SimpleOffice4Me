"""Safe rich-text handling for calendar descriptions."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_DESCRIPTION = 20_000
ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "blockquote", "pre", "code", "h1", "h2", "h3", "a"}
VOID_TAGS = {"br"}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "svg", "math"}:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED_TAGS:
            return
        safe_attrs = ""
        if tag == "a":
            href = next((value or "" for key, value in attrs if key.lower() == "href"), "").strip()
            parsed = urlsplit(href)
            if parsed.scheme.lower() in {"http", "https", "mailto"}:
                safe_attrs = f' href="{html.escape(href, quote=True)}" rel="noopener noreferrer"'
        self.parts.append(f"<{tag}{safe_attrs}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "svg", "math"} and self.skip:
            self.skip -= 1
        elif not self.skip and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(html.escape(data))


def sanitize_calendar_html(value: str) -> str:
    parser = _Sanitizer()
    parser.feed((value or "")[:MAX_DESCRIPTION])
    parser.close()
    return "".join(parser.parts).strip()


def html_to_text(value: str) -> str:
    value = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|blockquote|pre)\s*>", "\n", value)
    value = re.sub(r"<[^>]*>", "", value)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in html.unescape(value).splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_DESCRIPTION]


def split_content_line(line: str) -> tuple[str, str]:
    """Split an RFC 5545 content line at the first colon outside quotes."""
    quoted = escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            return line[:index], line[index + 1 :]
    return line, ""


def description_fields(text: str, rich: str = "", mode: str = "", existing: dict | None = None) -> dict[str, str]:
    existing = existing or {}
    if not rich and not mode:
        rich = str(existing.get("description_html", ""))
    if not mode:
        mode = str(existing.get("description_format", "text"))
    safe_html = sanitize_calendar_html(rich) if rich else ""
    normalized_text = (text or "").strip()[:MAX_DESCRIPTION]
    if safe_html and not normalized_text:
        normalized_text = html_to_text(safe_html)
    selected = "html" if mode == "html" and safe_html else "text"
    return {"reason": normalized_text, "description_html": safe_html, "description_format": selected}
