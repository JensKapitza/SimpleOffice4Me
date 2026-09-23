"""Small dependency-free safe Markdown renderer for project wiki/issues.

Raw HTML is always escaped. The renderer intentionally supports a conservative
GitHub-like subset instead of evaluating arbitrary HTML.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from markupsafe import Markup


_FENCE = re.compile(r"^\s*\x60\x60\x60([A-Za-z0-9_+.-]{0,40})\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ORDERED = re.compile(r"^\s*(\d{1,6})[.)]\s+(.+)$")
_UNORDERED = re.compile(r"^\s*[-*+]\s+(.+)$")
_TASK = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.+)$")
_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def _safe_url(value: str) -> str:
    raw = html.unescape(str(value or "")).strip()
    if not raw or any(ord(char) < 32 for char in raw):
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme:
        return raw if parsed.scheme.casefold() in {"http", "https", "mailto"} else ""
    if raw.startswith(("//", "\\")):
        return ""
    return raw


def _inline(value: str) -> str:
    """Escape first, then add a deliberately small set of safe inline markup."""

    escaped = html.escape(str(value or ""), quote=True)

    def link(match: re.Match[str]) -> str:
        label = match.group(1)
        href = _safe_url(match.group(2))
        if not href:
            return label
        safe_href = html.escape(href, quote=True)
        external = urlsplit(href).scheme.casefold() in {"http", "https"}
        attrs = ' rel="noopener noreferrer"' if external else ""
        return f'<a href="{safe_href}"{attrs}>{label}</a>'

    escaped = re.sub(r"\[([^\]\n]{1,500})\]\(([^)\n]{1,2000})\)", link, escaped)
    escaped = re.sub(r"\x60([^\x60\n]{1,2000})\x60", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__([^_\n]+)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"~~([^~\n]+)~~", r"<del>\1</del>", escaped)
    return escaped


def _table_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return len(cells) >= 2 and all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def render_markdown(value: str, *, maximum_chars: int = 200_000) -> Markup:
    """Render bounded Markdown to safe HTML without allowing raw HTML."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > maximum_chars:
        raise ValueError("Markdown content is too large")

    lines = text.split("\n")
    output: list[str] = []
    paragraph: list[str] = []
    list_kind = ""
    in_code = False
    code_language = ""
    code_lines: list[str] = []
    index = 0

    def close_paragraph() -> None:
        if paragraph:
            output.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = ""

    def start_list(kind: str) -> None:
        nonlocal list_kind
        close_paragraph()
        if list_kind and list_kind != kind:
            close_list()
        if not list_kind:
            output.append(f"<{kind}>")
            list_kind = kind

    while index < len(lines):
        line = lines[index]
        fence = _FENCE.match(line)
        if in_code:
            if fence:
                escaped_code = html.escape("\n".join(code_lines), quote=False)
                language = html.escape(code_language, quote=True)
                class_attr = f' class="language-{language}"' if language else ""
                output.append(f"<pre><code{class_attr}>{escaped_code}</code></pre>")
                in_code = False
                code_language = ""
                code_lines.clear()
            else:
                code_lines.append(line)
            index += 1
            continue

        if fence:
            close_paragraph()
            close_list()
            in_code = True
            code_language = fence.group(1)
            index += 1
            continue

        if not line.strip():
            close_paragraph()
            close_list()
            index += 1
            continue

        if "|" in line and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            close_paragraph()
            close_list()
            headers = _table_cells(line)
            output.append('<div class="table-responsive"><table class="table table-sm markdown-table"><thead><tr>')
            output.extend(f"<th>{_inline(cell)}</th>" for cell in headers)
            output.append("</tr></thead><tbody>")
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                cells = _table_cells(lines[index])
                output.append("<tr>")
                for position in range(len(headers)):
                    cell = cells[position] if position < len(cells) else ""
                    output.append(f"<td>{_inline(cell)}</td>")
                output.append("</tr>")
                index += 1
            output.append("</tbody></table></div>")
            continue

        heading = _HEADING.match(line)
        if heading:
            close_paragraph()
            close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        task = _TASK.match(line)
        if task:
            start_list("ul")
            checked = task.group(1).casefold() == "x"
            attrs = ' checked disabled' if checked else " disabled"
            output.append(
                '<li class="markdown-task"><input type="checkbox"'
                + attrs
                + f'> <span>{_inline(task.group(2))}</span></li>'
            )
            index += 1
            continue

        unordered = _UNORDERED.match(line)
        if unordered:
            start_list("ul")
            output.append(f"<li>{_inline(unordered.group(1))}</li>")
            index += 1
            continue

        ordered = _ORDERED.match(line)
        if ordered:
            start_list("ol")
            output.append(f"<li>{_inline(ordered.group(2))}</li>")
            index += 1
            continue

        if line.lstrip().startswith(">"):
            close_paragraph()
            close_list()
            quote = line.lstrip()[1:].lstrip()
            output.append(f"<blockquote>{_inline(quote)}</blockquote>")
            index += 1
            continue

        if re.fullmatch(r"\s*([-*_])(?:\s*\1){2,}\s*", line):
            close_paragraph()
            close_list()
            output.append("<hr>")
            index += 1
            continue

        if list_kind:
            close_list()
        paragraph.append(line)
        index += 1

    if in_code:
        escaped_code = html.escape("\n".join(code_lines), quote=False)
        language = html.escape(code_language, quote=True)
        class_attr = f' class="language-{language}"' if language else ""
        output.append(f"<pre><code{class_attr}>{escaped_code}</code></pre>")
    close_paragraph()
    close_list()
    return Markup("\n".join(output))
