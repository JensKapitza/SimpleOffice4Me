"""Inventory object suggestion schema (shared validation, no I/O).

Validates the structured photo-analysis output for the normal inventory
form and the photo series: object name, item type, confidence, a short
note, an exactly-visible ISBN (canonical checksum, never guessed), and
a few grounded tags (only directly visible features; an empty list is
correct). The model is never trusted: unknown types fall back, invalid
ISBNs are dropped, and only short deduplicated tag strings survive.
"""

from __future__ import annotations

from typing import Any

from ..inventory import normalize_isbn


class VisionError(ValueError):
    """Invalid image, invalid suggestion output, or unusable backend."""


OBJECT_ITEM_TYPES = frozenset(
    {
        "object", "book", "tool", "electrical-device", "machine", "vehicle",
        "it-device", "measuring-device", "ladder", "key", "furniture",
        "document", "spare-part", "consumable",
    }
)
OBJECT_NAME_LIMIT = 120
OBJECT_NOTE_LIMIT = 500
OBJECT_SUBTITLE_LIMIT = 240
OBJECT_AUTHORS_LIMIT = 6
OBJECT_TAGS_LIMIT = 6
OBJECT_TAG_LIMIT = 40


def normalize_object_tags(raw: object) -> list[str]:
    """Keep only short grounded tag strings (empty list stays valid)."""
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        tag = " ".join(entry.split())
        if not tag or len(tag) > OBJECT_TAG_LIMIT:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= OBJECT_TAGS_LIMIT:
            break
    return tags


def validate_object_suggestion(payload: object) -> dict[str, Any]:
    """Validate one inventory object suggestion (never trusts the model)."""
    if not isinstance(payload, dict):
        raise VisionError("suggestion output must be a JSON object")
    name = str(payload.get("object_name", "") or "").strip()
    if not name or len(name) > OBJECT_NAME_LIMIT:
        raise VisionError("suggestion needs an object name of 1-120 characters")
    item_type = str(payload.get("item_type", "") or "").strip().lower()
    if item_type not in OBJECT_ITEM_TYPES:
        item_type = "object"
    confidence = payload.get("confidence", 0)
    if isinstance(confidence, bool) or not isinstance(confidence, int):
        raise VisionError("suggestion confidence must be an integer 0-100")
    if not 0 <= confidence <= 100:
        raise VisionError("suggestion confidence must be an integer 0-100")
    note = str(payload.get("note", "") or "").strip()
    if len(note) > OBJECT_NOTE_LIMIT:
        raise VisionError("suggestion note is too long")
    subtitle = " ".join(str(payload.get("subtitle", "") or "").split())
    if len(subtitle) > OBJECT_SUBTITLE_LIMIT:
        subtitle = subtitle[:OBJECT_SUBTITLE_LIMIT]
    raw_authors = payload.get("authors", [])
    authors: list[str] = []
    if isinstance(raw_authors, list):
        for author in raw_authors:
            clean = " ".join(str(author or "").split())
            if clean and len(clean) <= 120 and clean.casefold() not in {
                value.casefold() for value in authors
            }:
                authors.append(clean)
            if len(authors) >= OBJECT_AUTHORS_LIMIT:
                break
    raw_isbn = payload.get("isbn", "")
    try:
        isbn = normalize_isbn(raw_isbn) if str(raw_isbn or "").strip() else ""
    except ValueError:
        isbn = ""
    return {
        "object_name": name,
        "item_type": item_type,
        "confidence": confidence,
        "note": note,
        "subtitle": subtitle,
        "authors": authors,
        "isbn": isbn,
        "tags": normalize_object_tags(payload.get("tags", [])),
    }


def object_instruction(hint: dict[str, str]) -> str:
    """Prompt for one grounded inventory object suggestion (JSON only)."""
    scanned = hint.get("isbn") or hint.get("barcode") or ""
    scanned_part = f" Gescannte Kennung (nur Hinweis, nie in den Namen übernehmen): {scanned}." if scanned else ""
    return (
        "Analysiere das Foto sorgfältig für die Inventarerfassung. Lies bei "
        "Büchern den gut lesbaren Haupttitel exakt ab und übernimm ihn in "
        "object_name (vollständiger Buchtitel samt Untertitel statt nur 'Buch'); "
        "gib den Untertitel zusätzlich separat in subtitle aus und erkannte "
        "Autorennamen als Liste in authors; achte auf die "
        "typografische Hierarchie: große/fette Schrift ist meist der Haupttitel, "
        "kleinere Schrift kann Untertitel, Reihe, Edition oder Beschreibung sein. "
        "Nenne im note-Feld erkannte Untertitel, Autor, Edition und auffällige "
        "Schrift-Hierarchie, sofern lesbar. Lies sichtbare ISBN-Zeichen exakt. "
        "Bei Gegenständen benenne konkret, was tatsächlich abgebildet ist "
        "(z. B. Hammer statt 'Gegenstand'). Erfinde oder vervollständige keine "
        "unleserlichen Wörter; kennzeichne Unsicherheit klar. Antworte NUR mit "
        "einem JSON-Objekt mit den Schlüsseln object_name (deutscher Titel oder "
        "Gegenstandsname, max 120 Zeichen), subtitle (sichtbarer Untertitel "
        "oder leerer String), authors (Liste sichtbar gedruckter Autoren, sonst "
        "leere Liste), item_type (genau "
        "einer: object, book, tool, electrical-device, machine, vehicle, "
        "it-device, measuring-device, ladder, key, furniture, document, "
        "spare-part, consumable), confidence (0-100), note (kurz, deutsch), "
        "isbn (nur exakt sichtbare ISBN-Zeichen aus dem Bild, sonst leerer "
        "String; eine ISBN niemals raten, ableiten oder ergänzen), tags "
        "(Liste mit höchstens 6 kurzen Schlagwörtern, nur direkt im Bild "
        "sichtbare oder klar erkannte Merkmale wie Farbe, Material oder "
        "Form; leere Liste ist korrekt; niemals Marken, Modelle, Preise "
        "oder Kennungen raten)."
        + scanned_part
    )
