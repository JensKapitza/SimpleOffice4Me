"""Bounded local cache for normalized ISBN provider metadata."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


BOOK_METADATA_CACHE_TTL_SECONDS = 24 * 60 * 60
BOOK_METADATA_CACHE_STALE_SECONDS = 30 * 24 * 60 * 60
MAX_BOOK_METADATA_CACHE_ENTRIES = 500
FIELD_LIMITS = {
    "isbn": 13,
    "title": 300,
    "subtitle": 300,
    "authors": 600,
    "publisher": 300,
    "published_date": 80,
    "description": 8000,
    "page_count": 20,
    "language": 80,
    "categories": 600,
    "market_price": 80,
    "currency": 3,
    "price_source": 120,
    "metadata_source": 300,
    "metadata_checked_at": 80,
}
CANDIDATE_FIELDS = ("metadata_source", "title", "authors", "publisher", "published_date")


def _line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _canonical_isbn(value: Any) -> str:
    isbn = str(value or "")
    if not re.fullmatch(r"(?:978|979)\d{10}", isbn):
        raise ValueError("canonical ISBN-13 required")
    return isbn


def _candidate(value: dict[str, Any]) -> dict[str, str]:
    return {
        key: cleaned
        for key in CANDIDATE_FIELDS
        if (cleaned := _line(value.get(key), 300))
    }


class BookMetadataCache:
    """Persist only bounded display fields, never provider responses or errors."""

    def __init__(self, root: str | Path):
        directory = Path(root).expanduser().resolve() / CONTROL_DIR / "inventory"
        self.path = directory / "book-metadata-cache.json"
        self.lock_path = directory / ".inventory-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def get(self, isbn: str, *, max_age_seconds: int = BOOK_METADATA_CACHE_TTL_SECONDS) -> dict[str, Any] | None:
        canonical = _canonical_isbn(isbn)
        entries = self._read().get("entries", {})
        entry = entries.get(canonical, {}) if isinstance(entries, dict) else {}
        metadata = entry.get("metadata", {}) if isinstance(entry, dict) else {}
        try:
            cached_at_epoch = float(entry.get("cached_at_epoch", 0))
        except (TypeError, ValueError):
            return None
        now = time.time()
        if (
            not isinstance(metadata, dict)
            or not metadata.get("title")
            or cached_at_epoch <= 0
            or cached_at_epoch > now + 300
            or now - cached_at_epoch > max_age_seconds
        ):
            return None
        result = dict(metadata)
        result["metadata_cached_at"] = _line(entry.get("cached_at"), 80)
        return result

    def put(self, isbn: str, metadata: dict[str, Any]) -> None:
        canonical = _canonical_isbn(isbn)
        cleaned: dict[str, Any] = {}
        for key, limit in FIELD_LIMITS.items():
            value = canonical if key == "isbn" else metadata.get(key)
            if not value:
                continue
            cleaned[key] = str(value).strip()[:limit] if key == "description" else _line(value, limit)
        candidates = metadata.get("metadata_candidates", [])
        if isinstance(candidates, list):
            cleaned["metadata_candidates"] = [
                result for row in candidates[:3] if isinstance(row, dict) and (result := _candidate(row))
            ]
        if not cleaned.get("title"):
            raise ValueError("Buchmetadaten ohne Titel werden nicht gecacht")

        now = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            entries = state.get("entries", {})
            if not isinstance(entries, dict):
                entries = {}
            entries[canonical] = {"cached_at": utc_now(), "cached_at_epoch": now, "metadata": cleaned}
            valid = [
                (key, value)
                for key, value in entries.items()
                if re.fullmatch(r"(?:978|979)\d{10}", str(key)) and isinstance(value, dict)
            ]
            valid.sort(
                key=lambda row: float(row[1].get("cached_at_epoch", 0))
                if isinstance(row[1].get("cached_at_epoch"), (int, float))
                else 0,
                reverse=True,
            )
            state["entries"] = dict(valid[:MAX_BOOK_METADATA_CACHE_ENTRIES])
            state["version"] = 1
            atomic_json_write(self.path, state)
