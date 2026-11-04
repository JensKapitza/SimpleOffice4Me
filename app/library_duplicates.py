"""Local library duplicate suggestions (no network, stores nothing).

Groups books with the same normalized ISBN so double cataloguing can be
resolved through the normal library and object routes after explicit
confirmation. Proposals are read-only with justifications and never
change data.
"""

from __future__ import annotations

import re
from typing import Any

MAX_GROUPS = 5
MAX_DUPLICATE_IDS = 10
_ISBN_CLEAN_RE = re.compile(r"[\s\-.]+")


def normalize_isbn(raw: object) -> str:
    """Normalize an ISBN for comparison (digits plus trailing X)."""
    text = _ISBN_CLEAN_RE.sub("", str(raw or "")).upper()
    normalized = "".join(char for char in text if char.isdigit())
    if text.endswith("X"):
        normalized += "X"
    return normalized if len(normalized) in {10, 13} else ""


def _book_isbn(book: dict[str, Any]) -> str:
    fields = book.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}
    for raw in (fields.get("isbn", ""), book.get("identifier", "")):
        normalized = normalize_isbn(raw)
        if normalized:
            return normalized
    return ""


def _book_title(book: dict[str, Any]) -> str:
    fields = book.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}
    for raw in (fields.get("title", ""), book.get("name", "")):
        text = str(raw or "").strip()
        if text:
            return text
    return "ohne Titel"


def suggest_library_duplicates(
    books: list[dict[str, Any]],
    max_groups: int = MAX_GROUPS,
) -> list[dict[str, Any]]:
    """Propose books sharing one ISBN (never writes)."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for book in books or []:
        if not isinstance(book, dict):
            continue
        key = _book_isbn(book)
        if not key:
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(book)
    proposals: list[dict[str, Any]] = []
    for key in order:
        group = buckets[key]
        if len(group) < 2:
            continue
        title = _book_title(group[0])
        proposals.append(
            {
                "isbn": key,
                "title": title,
                "object_ids": [
                    str(row.get("object_id", "") or "")
                    for row in group[:MAX_DUPLICATE_IDS + 1]
                ],
                "count": len(group),
                "reason": (
                    f"{len(group)} Einträge mit ISBN {key} („{title}“) — "
                    "Dubletten prüfen und über die Objektseite bereinigen."
                ),
            }
        )
    proposals.sort(key=lambda row: (-int(row["count"]), str(row["title"]).casefold()))
    return proposals[: max(0, int(max_groups))]
