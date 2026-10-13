"""Standards-oriented metadata serializers for library export endpoints."""
from __future__ import annotations

import re
from collections.abc import Mapping


def _text(value: object) -> str:
    """Return one-line metadata so values cannot inject export records."""
    if value is None:
        return ""
    return " ".join(str(value).replace("\x00", "").split())


def _authors(value: object) -> list[str]:
    source = value if isinstance(value, (list, tuple)) else re.split(r"[;\n]+", str(value or ""))
    return [clean for entry in source if (clean := _text(entry))]


def _record_values(item: Mapping, placement: Mapping) -> dict[str, str]:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    location = _text(placement.get("path") or item.get("location", ""))
    inventory_id = _text(item.get("display_id", ""))
    local_note = "; ".join(value for value in (
        f"Inventory ID: {inventory_id}" if inventory_id else "",
        f"Library location: {location}" if location else "",
        f"Pages: {_text(fields.get('pages', ''))}" if _text(fields.get("pages", "")) else "",
    ) if value)
    note = "; ".join(value for value in (
        local_note,
        f"ISBN: {_text(fields.get('isbn') or item.get('identifier', ''))}" if _text(fields.get("isbn") or item.get("identifier", "")) else "",
        f"Language: {_text(fields.get('language', ''))}" if _text(fields.get("language", "")) else "",
        f"Genre: {_text(fields.get('genre', ''))}" if _text(fields.get("genre", "")) else "",
        f"Description: {_text(fields.get('description', '') or item.get('description', ''))}" if _text(fields.get("description", "") or item.get("description", "")) else "",
    ) if value)
    return {
        "title": _text(item.get("name", "")),
        "authors": "; ".join(_authors(fields.get("authors", ""))),
        "isbn": _text(fields.get("isbn") or item.get("identifier", "")),
        "genre": _text(fields.get("genre", "")),
        "series": _text(fields.get("series", "")),
        "series_number": _text(fields.get("series_number", "")),
        "publisher": _text(fields.get("publisher", "")),
        "year": _text(fields.get("year", "") or fields.get("publication_year", "")),
        "language": _text(fields.get("language", "")),
        "edition": _text(fields.get("edition", "")),
        "pages": _text(fields.get("pages", "")),
        "description": _text(fields.get("description", "") or item.get("description", "")),
        "note": note,
        "local_note": local_note,
    }


def ris_record(item: Mapping, placement: Mapping) -> str:
    """Serialize one book using common RIS BOOK tags; preserve series/location notes."""
    values = _record_values(item, placement)
    lines = ["TY  - BOOK"]
    if item.get("object_id"):
        lines.append(f"ID  - {_text(item['object_id'])}")
    if values["title"]:
        lines.append(f"T1  - {values['title']}")
    lines.extend(f"AU  - {author}" for author in _authors(values["authors"]))
    for tag, key in (
        ("SN", "isbn"), ("PB", "publisher"), ("PY", "year"),
        ("LA", "language"), ("ET", "edition"), ("T3", "series"),
        ("VL", "series_number"), ("KW", "genre"),
        ("AB", "description"), ("N1", "local_note"),
    ):
        if values[key]:
            lines.append(f"{tag}  - {values[key]}")
    lines.append("ER  -")
    return "\r\n".join(lines) + "\r\n\r\n"


def _bibtex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
        "$": r"\$", "%": r"\%", "&": r"\&", "#": r"\#",
        "_": r"\_",
    }
    return "".join(replacements.get(character, character) for character in value)


def bibtex_record(item: Mapping, placement: Mapping) -> str:
    """Serialize one book as BibTeX without allowing metadata to break field syntax."""
    values = _record_values(item, placement)
    raw_key = _text(item.get("object_id", "")) or _text(item.get("display_id", "book"))
    key = re.sub(r"[^A-Za-z0-9:_-]", "_", raw_key) or "book"
    fields = (
        ("title", values["title"]),
        ("author", " and ".join(_authors(values["authors"]))),
        ("publisher", values["publisher"]),
        ("year", values["year"]),
        ("edition", values["edition"]),
        ("series", values["series"]),
        ("volume", values["series_number"]),
        ("note", values["note"]),
    )
    rendered_fields = [(name, value) for name, value in fields if value]
    lines = [f"@book{{simpleoffice_{key},"]
    for index, (name, value) in enumerate(rendered_fields):
        comma = "," if index < len(rendered_fields) - 1 else ""
        lines.append(f"  {name} = {{{_bibtex_escape(value)}}}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n\n"
