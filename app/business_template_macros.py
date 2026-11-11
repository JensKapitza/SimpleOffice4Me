"""Safe token and inline-format macro language for business templates.

This module never generates HTML and never evaluates code. It turns a small whitelist
of tags into plain-text style runs for browser preview and PDF rendering.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

MAX_MACRO_TEXT = 10_000
MAX_MACRO_NESTING = 16
TOKEN_RE = re.compile(r"\{\{([a-z0-9_.-]{1,80})\}\}", re.IGNORECASE)
TAG_RE = re.compile(r"\[(/?)(B|I|U|SIZE|COLOR|ALIGN)(?:=([^\]]+))?\]", re.IGNORECASE)
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
ALIGNMENTS = {"left", "center", "right", "justify"}


class MacroSyntaxError(ValueError):
    """Raised when supported markup is malformed or outside safe limits."""


def macro_tokens(text: str) -> set[str]:
    return {match.group(1) for match in TOKEN_RE.finditer(str(text))}


def resolve_tokens(text: str, values: Mapping[str, Any], *, keep_unknown: bool = True) -> str:
    source = str(text)
    if len(source) > MAX_MACRO_TEXT:
        raise MacroSyntaxError("macro text exceeds the maximum length")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0) if keep_unknown else ""
        value = values[key]
        return "" if value is None else str(value)

    seen = {source}
    for _pass in range(8):
        expanded = TOKEN_RE.sub(replace, source)
        if len(expanded) > MAX_MACRO_TEXT:
            raise MacroSyntaxError("expanded macro text exceeds the maximum length")
        if expanded == source:
            return expanded
        if expanded in seen:
            raise MacroSyntaxError("token expansion cycle detected")
        seen.add(expanded)
        source = expanded
    if TOKEN_RE.search(source):
        raise MacroSyntaxError("token expansion depth exceeded")
    return source


def _default_style() -> dict[str, Any]:
    return {
        "bold": False,
        "italic": False,
        "underline": False,
        "font_size": None,
        "color": None,
        "align": None,
    }


def _open_tag(style: dict[str, Any], tag: str, argument: str | None) -> None:
    if tag == "B":
        if argument is not None:
            raise MacroSyntaxError("B does not accept an argument")
        style["bold"] = True
    elif tag == "I":
        if argument is not None:
            raise MacroSyntaxError("I does not accept an argument")
        style["italic"] = True
    elif tag == "U":
        if argument is not None:
            raise MacroSyntaxError("U does not accept an argument")
        style["underline"] = True
    elif tag == "SIZE":
        if argument is None:
            raise MacroSyntaxError("SIZE requires a numeric argument")
        try:
            size = float(argument)
        except ValueError as exc:
            raise MacroSyntaxError("SIZE requires a numeric argument") from exc
        if not 6 <= size <= 72:
            raise MacroSyntaxError("SIZE must be between 6 and 72")
        style["font_size"] = round(size, 2)
    elif tag == "COLOR":
        color = str(argument or "")
        if not COLOR_RE.fullmatch(color):
            raise MacroSyntaxError("COLOR must use #RRGGBB")
        style["color"] = color.upper()
    elif tag == "ALIGN":
        align = str(argument or "").lower()
        if align not in ALIGNMENTS:
            raise MacroSyntaxError("ALIGN must be left, center, right or justify")
        style["align"] = align


def rich_text_runs(text: str, values: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Parse safe markup into ordered plain-text style runs."""
    source = resolve_tokens(str(text), values or {}, keep_unknown=True)
    if len(source) > MAX_MACRO_TEXT:
        raise MacroSyntaxError("macro text exceeds the maximum length")

    style = _default_style()
    stack: list[tuple[str, dict[str, Any]]] = []
    runs: list[dict[str, Any]] = []
    position = 0

    def append(fragment: str) -> None:
        if not fragment:
            return
        run = {"text": fragment, **style}
        if runs and {k: v for k, v in runs[-1].items() if k != "text"} == {k: v for k, v in run.items() if k != "text"}:
            runs[-1]["text"] += fragment
        else:
            runs.append(run)

    for match in TAG_RE.finditer(source):
        append(source[position:match.start()])
        closing, raw_tag, argument = match.groups()
        tag = raw_tag.upper()
        if closing:
            if argument is not None:
                raise MacroSyntaxError("closing macro tags cannot have arguments")
            if not stack or stack[-1][0] != tag:
                raise MacroSyntaxError(f"macro closing tag {tag} is not properly nested")
            _tag, previous = stack.pop()
            style = previous
        else:
            if len(stack) >= MAX_MACRO_NESTING:
                raise MacroSyntaxError("macro nesting limit exceeded")
            previous = dict(style)
            _open_tag(style, tag, argument)
            stack.append((tag, previous))
        position = match.end()

    append(source[position:])
    if stack:
        raise MacroSyntaxError(f"macro tag {stack[-1][0]} is not closed")
    return runs


def validate_macro_text(text: str) -> None:
    rich_text_runs(text, {})


def macro_diagnostics(text: str, known_tokens: set[str] | None = None) -> dict[str, Any]:
    known = known_tokens or set()
    unknown = sorted(token for token in macro_tokens(text) if token not in known)
    try:
        rich_text_runs(text, {})
        error = ""
    except MacroSyntaxError as exc:
        error = str(exc)
    return {"unknown_tokens": unknown, "syntax_error": error}
