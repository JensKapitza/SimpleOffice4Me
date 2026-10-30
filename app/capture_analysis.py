"""Pure helpers for the photo capture inbox (books vs objects).

All guesses are labeled with their source (OCR text, ISBN pattern,
local ML label, cached metadata). Nothing here invents authors or
titles: missing data is reported as missing fields for the user to
correct before adopting a draft.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .inventory import normalize_isbn

_ISBN13_RE = re.compile(r"(?<!\d)(97[89]\d{10})(?!\d)")
_ISBN13_LOOSE_RE = re.compile(r"(?<!\d)(97[89](?:[\- ]?\d){10})(?!\d)")
_ISBN10_RE = re.compile(r"(?<!\d)(\d{9}[0-9Xx])(?!\d)")
_ISBN10_LOOSE_RE = re.compile(r"(?<!\d)(\d(?:[\- ]?\d){8}[\- ]?[0-9Xx])(?!\d)")
_SEP_RE = re.compile(r"[\- ]")
_YEAR_RE = re.compile(r"(?<!\d)((?:1[5-9]|20)\d{2})(?!\d)")
_MAX_LINE = 120
_MAX_TITLE_LINES = 3


def isbn_from_text(text: str) -> str:
    """Return the first checksum-valid ISBN found in free text, else "".

    ISBN-13 candidates (978/979, plain or with hyphens/spaces) win over
    ISBN-10; every candidate is separator-stripped and validated with
    the shared checksum rules.
    """
    cleaned = str(text or "")
    for pattern in (_ISBN13_RE, _ISBN13_LOOSE_RE, _ISBN10_RE, _ISBN10_LOOSE_RE):
        for match in pattern.finditer(cleaned):
            try:
                return normalize_isbn(_SEP_RE.sub("", match.group(1)))
            except ValueError:
                continue
    return ""


def extract_year(text: str) -> str:
    """Return the first plausible publication year in free text, else ""."""
    current = datetime.now().year
    for match in _YEAR_RE.finditer(str(text or "")):
        year = int(match.group(1))
        if 1500 <= year <= current + 1:
            return str(year)
    return ""


def title_lines(text: str, limit: int = _MAX_TITLE_LINES) -> list[str]:
    """Return candidate title lines (OCR order) for the user to pick from."""
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = " ".join(raw.split())[:_MAX_LINE].strip()
        if not line or isbn_from_text(line):
            continue
        if line not in lines:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def route_kind(
    isbn: str, ml_label: str, ml_available: bool, ml_item_type: str = ""
) -> dict[str, Any]:
    """Route a capture to the book or object flow with an honest reason."""
    if isbn:
        return {
            "kind": "book",
            "reason": "ISBN im Foto erkannt",
            "needs_name": False,
        }
    if ml_available and str(ml_item_type or "").strip().lower() == "book":
        return {
            "kind": "book",
            "reason": "Buch erkannt (ML-Typ), ISBN fehlt",
            "needs_name": False,
        }
    label = " ".join(str(ml_label or "").split())[:_MAX_LINE]
    if ml_available and label:
        return {
            "kind": "object",
            "reason": f"Objekt erkannt: {label}",
            "needs_name": False,
        }
    return {
        "kind": "object",
        "reason": "manuell einordnen",
        "needs_name": True,
    }


def missing_book_fields(prefill: dict[str, Any]) -> list[str]:
    """List empty book fields the user must complete before adopting."""
    missing = []
    if not str(prefill.get("title", "") or "").strip():
        missing.append("title")
    if not prefill.get("authors"):
        missing.append("authors")
    if not str(prefill.get("year", "") or "").strip():
        missing.append("year")
    if not str(prefill.get("isbn", "") or "").strip():
        missing.append("isbn")
    return missing


def combine_batch_analyses(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-photo payloads of one book group (pure, no guessing).

    The first valid ISBN wins; differing ISBNs across photos set
    ``isbn_conflict`` and clear the merged ISBN so the user must pick
    the correct one. Titles, authors, year and publisher take the
    first non-empty value in capture order; title lines are deduped
    across photos. Duplicates are merged by object id, ML models are
    listed honestly per photo, and every photo keeps its own
    breakdown — nothing is silently dropped.
    """
    rows = [row for row in (analyses or []) if isinstance(row, dict)]
    photos: list[dict[str, Any]] = []
    isbns: list[str] = []
    titles: list[str] = []
    authors: list[str] = []
    years: list[str] = []
    publishers: list[str] = []
    merged_lines: list[str] = []
    duplicates: dict[str, dict[str, Any]] = {}
    models: list[str] = []
    book_votes = 0
    analyzed = 0
    for row in rows:
        if not row.get("ok"):
            continue
        analyzed += 1
        photo_id = str(row.get("photo_id", "") or "")
        isbn = str(row.get("isbn", "") or "").strip()
        if isbn and isbn not in isbns:
            isbns.append(isbn)
        prefill = row.get("prefill", {})
        if not isinstance(prefill, dict):
            prefill = {}
        title = str(prefill.get("title", "") or "").strip()
        if title:
            titles.append(title)
        for name in prefill.get("authors", []) or []:
            clean = str(name or "").strip()
            if clean and clean not in authors:
                authors.append(clean)
        year = str(prefill.get("year", "") or "").strip()
        if year:
            years.append(year)
        publisher = str(prefill.get("publisher", "") or "").strip()
        if publisher:
            publishers.append(publisher)
        for line in prefill.get("title_lines", []) or []:
            clean = " ".join(str(line or "").split())[:_MAX_LINE].strip()
            if clean and clean not in merged_lines and not isbn_from_text(clean):
                merged_lines.append(clean)
        for dup in row.get("duplicates", []) or []:
            if isinstance(dup, dict) and dup.get("object_id"):
                duplicates.setdefault(str(dup["object_id"]), dict(dup))
        ml = row.get("ml", {})
        if not isinstance(ml, dict):
            ml = {}
        model = " ".join(str(ml.get("model", "") or "").split())[:_MAX_LINE]
        if ml.get("available") and model and model not in models:
            models.append(model)
        if str(row.get("kind", "") or "") == "book":
            book_votes += 1
        ocr = row.get("ocr", {})
        if not isinstance(ocr, dict):
            ocr = {}
        photos.append({
            "photo_id": photo_id,
            "isbn": isbn,
            "kind": str(row.get("kind", "") or ""),
            "kind_reason": str(row.get("kind_reason", "") or ""),
            "ml_available": bool(ml.get("available")),
            "ml_label": str(ml.get("label", "") or ""),
            "ml_model": model,
            "ml_provider_label": str(ml.get("provider_label", "") or ""),
            "ml_error": str(ml.get("error", "") or ""),
            "ocr_engine": str(ocr.get("engine", "") or ""),
            "ocr_status": str(ocr.get("status", "") or ""),
        })
    isbn_conflict = len(isbns) > 1
    isbn = "" if isbn_conflict else (isbns[0] if isbns else "")
    warnings: list[str] = []
    if isbn_conflict:
        warnings.append(
            "Fotos zeigen unterschiedliche ISBNs — "
            "bitte korrekte ISBN wählen."
        )
    if analyzed and analyzed < len(rows):
        warnings.append(
            "Nicht alle Fotos konnten analysiert werden — "
            "Einzelwerte bleiben erhalten."
        )
    if isbn:
        kind: dict[str, Any] = {
            "kind": "book",
            "reason": "ISBN in der Buchgruppe erkannt",
            "needs_name": False,
        }
    elif book_votes:
        kind = {
            "kind": "book",
            "reason": f"Buch in {book_votes} von {analyzed} Fotos",
            "needs_name": False,
        }
    else:
        kind = {"kind": "object", "reason": "manuell einordnen",
                "needs_name": True}
    prefill_merged: dict[str, Any] = {"isbn": isbn}
    if kind["kind"] == "book":
        prefill_merged.update({
            "title": titles[0] if titles else "",
            "authors": authors,
            "year": years[0] if years else "",
            "publisher": publishers[0] if publishers else "",
            "title_lines": merged_lines[:9],
        })
        missing = missing_book_fields(prefill_merged)
    else:
        first_label = next(
            (photo["ml_label"] for photo in photos if photo["ml_label"]), "")
        prefill_merged.update({"name": first_label})
        missing = [] if first_label else ["name"]
    if isbn_conflict and "isbn" not in missing:
        missing.append("isbn")
    return {
        "photo_count": len(rows),
        "analyzed_count": analyzed,
        "isbn": isbn,
        "isbn_conflict": isbn_conflict,
        "isbn_candidates": isbns,
        "kind": kind["kind"],
        "kind_reason": kind["reason"],
        "needs_name": kind["needs_name"],
        "prefill": prefill_merged,
        "duplicates": list(duplicates.values()),
        "missing_fields": missing,
        "ml_models": models,
        "photos": photos,
        "warnings": warnings,
    }
