"""Standards-oriented metadata serializers for library export endpoints."""
from __future__ import annotations

import re
from collections.abc import Mapping
import xml.etree.ElementTree as ET


CALIBRE_CSV_HEADERS = (
    "title", "authors", "series", "series_index", "publisher", "published",
    "identifiers", "tags", "comments", "language",
)
_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
ET.register_namespace("dc", _DC_NS)
ET.register_namespace("opf", _OPF_NS)


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
        _text(fields.get("bibliography_note", "")),
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


def _calibre_text(value: object) -> str:
    """Remove code points forbidden by XML 1.0 before CSV/OPF serialization."""
    text = _text(value)
    return "".join(
        character for character in text
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
    )


def _calibre_comments(values: Mapping[str, str]) -> str:
    details = [values["description"], values["local_note"]]
    if values["edition"]:
        details.append(f"Edition: {values['edition']}")
    return "; ".join(detail for detail in details if detail)


def calibre_csv_values(item: Mapping, placement: Mapping) -> tuple[str, ...]:
    """Map one existing inventory record to Calibre's standard CSV columns."""
    values = _record_values(item, placement)
    identifiers = f"isbn:{values['isbn']}" if values["isbn"] else ""
    comments = _calibre_comments(values)
    return tuple(_calibre_text(value) for value in (
        values["title"], " & ".join(_authors(values["authors"])), values["series"],
        values["series_number"], values["publisher"], values["year"], identifiers,
        values["genre"], comments, values["language"],
    ))


def calibre_opf_record(item: Mapping, placement: Mapping) -> bytes:
    """Serialize one metadata-only Calibre OPF sidecar for an inventory book."""
    values = _record_values(item, placement)
    values = {key: _calibre_text(value) for key, value in values.items()}
    package = ET.Element(f"{{{_OPF_NS}}}package", {
        "version": "2.0", "unique-identifier": "simpleoffice-id",
    })
    metadata = ET.SubElement(package, f"{{{_OPF_NS}}}metadata")

    def add_dc(name: str, value: str, attributes: dict[str, str] | None = None) -> None:
        if value:
            ET.SubElement(metadata, f"{{{_DC_NS}}}{name}", attributes or {}).text = value

    add_dc("title", values["title"])
    for author in _authors(values["authors"]):
        add_dc("creator", author, {f"{{{_OPF_NS}}}role": "aut"})
    add_dc("identifier", _calibre_text(item.get("object_id") or item.get("display_id") or ""), {
        "id": "simpleoffice-id", f"{{{_OPF_NS}}}scheme": "URI",
    })
    if values["isbn"]:
        add_dc("identifier", values["isbn"], {"id": "isbn", f"{{{_OPF_NS}}}scheme": "ISBN"})
    add_dc("language", values["language"] or "und")
    add_dc("publisher", values["publisher"])
    add_dc("date", values["year"])
    add_dc("subject", values["genre"])
    add_dc("description", _calibre_comments(values))
    if values["series"]:
        ET.SubElement(metadata, f"{{{_OPF_NS}}}meta", {
            "name": "calibre:series", "content": values["series"],
        })
    if values["series_number"]:
        ET.SubElement(metadata, f"{{{_OPF_NS}}}meta", {
            "name": "calibre:series_index", "content": values["series_number"],
        })
    ET.SubElement(package, f"{{{_OPF_NS}}}manifest")
    ET.SubElement(package, f"{{{_OPF_NS}}}spine")
    return ET.tostring(package, encoding="utf-8", xml_declaration=True)


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
