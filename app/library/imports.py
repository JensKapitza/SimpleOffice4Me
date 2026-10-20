"""Bounded parsers for library metadata import formats."""
from __future__ import annotations

import csv
import io
import re
import zipfile


def calibre_import_rows(upload: bytes) -> list[dict[str, str]]:
    """Read a bounded Calibre metadata.csv without extracting archive members."""
    if not upload or len(upload) > 12 * 1024 * 1024:
        raise ValueError("Die Calibre-Datei fehlt oder ist zu groß.")
    try:
        with zipfile.ZipFile(io.BytesIO(upload)) as archive:
            metadata = archive.getinfo("metadata.csv")
            if metadata.file_size > 10 * 1024 * 1024:
                raise ValueError("metadata.csv ist zu groß.")
            if sum(info.file_size for info in archive.infolist()) > 20 * 1024 * 1024:
                raise ValueError("Das Archiv ist nach dem Entpacken zu groß.")
            raw = archive.read(metadata)
    except (KeyError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise ValueError("Ungültiges Calibre-Archiv.") from exc
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        required = {
            "title", "authors", "series", "series_index", "publisher", "published",
            "identifiers", "tags", "comments", "language",
        }
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("metadata.csv hat nicht das unterstützte Calibre-Format.")
        rows = []
        for row in reader:
            if len(rows) >= 500:
                raise ValueError("Es dürfen höchstens 500 Bücher importiert werden.")
            clean = {
                key: " ".join(str(row.get(key, "") or "").replace("\x00", "").split())[:4000]
                for key in required
            }
            if clean["title"]:
                rows.append(clean)
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("metadata.csv konnte nicht gelesen werden.") from exc


def calibre_isbn(value: str) -> str:
    for candidate in str(value or "").split(","):
        key, _, isbn = candidate.partition(":")
        if key.strip().casefold() == "isbn" and isbn.strip():
            return isbn.strip()[:64]
    return ""


def ris_import_rows(upload: bytes) -> list[dict[str, str]]:
    """Parse bounded BOOK/EBOOK records from UTF-8 RIS without silent truncation."""
    if not upload or len(upload) > 4 * 1024 * 1024:
        raise ValueError("Die RIS-Datei fehlt oder ist zu groß.")
    try:
        text = upload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Die RIS-Datei muss UTF-8-codiert sein.") from exc

    rows: list[dict[str, str]] = []
    record: dict[str, list[str]] | None = None
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip():
            continue
        match = re.fullmatch(r"([A-Z0-9]{2})  - ?(.*)", raw_line)
        if match is None:
            raise ValueError(f"Ungültige RIS-Zeile {line_number}.")
        tag, raw_value = match.groups()
        value = " ".join(raw_value.replace("\x00", "").split())
        if len(value) > 4000:
            raise ValueError(f"RIS-Wert in Zeile {line_number} ist zu lang.")
        if tag == "TY":
            if record is not None:
                raise ValueError("Ein RIS-Datensatz wurde nicht mit ER abgeschlossen.")
            record = {"TY": [value.upper()]}
            continue
        if record is None:
            raise ValueError(f"RIS-Zeile {line_number} steht außerhalb eines Datensatzes.")
        if tag == "ER":
            _append_ris_record(rows, record)
            record = None
            continue
        record.setdefault(tag, []).append(value)

    if record is not None:
        raise ValueError("Ein RIS-Datensatz wurde nicht mit ER abgeschlossen.")
    if not rows:
        raise ValueError("Die RIS-Datei enthält keine importierbaren Bücher.")
    return rows


def _append_ris_record(rows: list[dict[str, str]], record: dict[str, list[str]]) -> None:
    kind = (record.get("TY") or [""])[0]
    if kind not in {"BOOK", "EBOOK"}:
        raise ValueError("Der RIS-Import unterstützt nur BOOK- und EBOOK-Datensätze.")
    title = next(
        ((record.get(candidate) or [""])[0] for candidate in ("T1", "TI", "CT") if record.get(candidate)),
        "",
    )
    if not title:
        raise ValueError("Ein RIS-Buchdatensatz benötigt einen Titel.")
    isbn = (record.get("SN") or [""])[0]
    if len(isbn) > 64:
        raise ValueError("Die ISBN im RIS-Datensatz ist zu lang.")
    rows.append({
        "title": title,
        "authors": "; ".join(record.get("AU", []) or record.get("A1", [])),
        "isbn": isbn,
        "genre": "; ".join(record.get("KW", [])),
        "series": (record.get("T3") or [""])[0],
        "series_number": (record.get("VL") or [""])[0],
        "publisher": (record.get("PB") or [""])[0],
        "year": (record.get("PY") or record.get("Y1") or [""])[0],
        "language": (record.get("LA") or [""])[0],
        "edition": (record.get("ET") or [""])[0],
        "description": (record.get("AB") or [""])[0],
        "bibliography_note": "; ".join(record.get("N1", [])),
    })
    if len(rows) > 500:
        raise ValueError("Es dürfen höchstens 500 Bücher importiert werden.")


_BIBTEX_MAX_UPLOAD = 4 * 1024 * 1024
_BIBTEX_MAX_RECORDS = 500
_BIBTEX_MAX_VALUE = 4000


def bibtex_import_rows(upload: bytes) -> list[dict[str, str]]:
    """Parse bounded UTF-8 @book records without executing BibTeX commands."""
    if not upload or len(upload) > _BIBTEX_MAX_UPLOAD:
        raise ValueError("Die BibTeX-Datei fehlt oder ist zu groß.")
    try:
        text = upload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Die BibTeX-Datei muss UTF-8-codiert sein.") from exc

    rows: list[dict[str, str]] = []
    offset = 0
    while True:
        offset = _skip_bibtex_space_and_comments(text, offset)
        if offset == len(text):
            break
        entry, offset = _read_bibtex_entry(text, offset)
        rows.append(_bibtex_row(entry))
        if len(rows) > _BIBTEX_MAX_RECORDS:
            raise ValueError("Es dürfen höchstens 500 Bücher importiert werden.")
    if not rows:
        raise ValueError("Die BibTeX-Datei enthält keine importierbaren Bücher.")
    return rows


def _skip_bibtex_space_and_comments(text: str, offset: int) -> int:
    while offset < len(text):
        if text[offset].isspace():
            offset += 1
        elif text[offset] == "%":
            newline = text.find("\n", offset)
            offset = len(text) if newline < 0 else newline + 1
        else:
            break
    return offset


def _read_bibtex_entry(text: str, offset: int) -> tuple[dict[str, str], int]:
    match = re.match(r"@([A-Za-z]+)\s*([({])", text[offset:])
    if match is None:
        raise ValueError("Ungültiger BibTeX-Datensatz.")
    kind = match.group(1).casefold()
    opening = match.group(2)
    closing = "}" if opening == "{" else ")"
    cursor = offset + match.end()
    body_start = cursor
    outer_depth = 1
    brace_depth = 0
    quoted = False
    escaped = False
    while cursor < len(text):
        character = text[cursor]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"' and brace_depth == 0:
            quoted = not quoted
        elif not quoted:
            if opening == "{" and character == "{":
                outer_depth += 1
            elif opening == "{" and character == "}":
                outer_depth -= 1
                if outer_depth == 0:
                    return _parse_bibtex_entry_body(kind, text[body_start:cursor]), cursor + 1
            elif opening == "(" and character == "{":
                brace_depth += 1
            elif opening == "(" and character == "}" and brace_depth:
                brace_depth -= 1
            elif opening == "(" and character == closing and brace_depth == 0:
                return _parse_bibtex_entry_body(kind, text[body_start:cursor]), cursor + 1
        cursor += 1
    raise ValueError("Ein BibTeX-Datensatz wurde nicht abgeschlossen.")


def _parse_bibtex_entry_body(kind: str, body: str) -> dict[str, str]:
    if kind != "book":
        raise ValueError("Der BibTeX-Import unterstützt nur @book-Datensätze.")
    comma = _bibtex_top_level_delimiter(body, ",")
    citation_key = body[:comma].strip() if comma >= 0 else ""
    if not citation_key or len(citation_key) > 256:
        raise ValueError("Ein BibTeX-Buchdatensatz benötigt einen kurzen Schlüssel.")
    fields: dict[str, str] = {}
    cursor = comma + 1
    while True:
        cursor = _skip_bibtex_separators(body, cursor)
        if cursor == len(body):
            break
        name_start = cursor
        if not _is_ascii_letter(body[cursor]):
            raise ValueError("Ungültiges BibTeX-Feld.")
        cursor += 1
        while cursor < len(body) and _is_bibtex_field_name_character(body[cursor]):
            cursor += 1
        name = body[name_start:cursor].casefold()
        if name in fields:
            raise ValueError(f"Doppeltes BibTeX-Feld: {name}.")
        cursor = _skip_bibtex_space(body, cursor)
        if cursor == len(body) or body[cursor] != "=":
            raise ValueError("Ungültiges BibTeX-Feld.")
        cursor = _skip_bibtex_space(body, cursor + 1)
        value, cursor = _read_bibtex_value(body, cursor)
        fields[name] = value
        cursor = _skip_bibtex_space(body, cursor)
        if cursor < len(body) and body[cursor] != ",":
            raise ValueError("BibTeX-Felder müssen durch Kommas getrennt sein.")
    return fields


def _is_ascii_letter(character: str) -> bool:
    return "A" <= character <= "Z" or "a" <= character <= "z"


def _is_bibtex_field_name_character(character: str) -> bool:
    return (
        _is_ascii_letter(character)
        or "0" <= character <= "9"
        or character in "_-"
    )


def _skip_bibtex_space(text: str, offset: int) -> int:
    while offset < len(text) and text[offset].isspace():
        offset += 1
    return offset


def _skip_bibtex_separators(text: str, offset: int) -> int:
    while offset < len(text) and (text[offset].isspace() or text[offset] == ","):
        offset += 1
    return offset


def _read_bibtex_value(body: str, offset: int) -> tuple[str, int]:
    if offset >= len(body):
        raise ValueError("BibTeX-Feldwert fehlt.")
    if body[offset] == "{":
        raw, cursor = _read_bibtex_delimited(body, offset, "{", "}", nested=True)
    elif body[offset] == '"':
        raw, cursor = _read_bibtex_delimited(body, offset, '"', '"', nested=False)
    else:
        cursor = offset
        while cursor < len(body) and body[cursor] not in ", \t\r\n":
            cursor += 1
        raw = body[offset:cursor]
        if not re.fullmatch(r"[A-Za-z0-9.:_+/-]+", raw):
            raise ValueError("Ungültiger ungeklammerter BibTeX-Wert.")
    value = _bibtex_unescape(raw)
    if len(value) > _BIBTEX_MAX_VALUE:
        raise ValueError("Ein BibTeX-Feldwert ist zu lang.")
    return value, cursor


def _read_bibtex_delimited(
    text: str,
    offset: int,
    opening: str,
    closing: str,
    *,
    nested: bool,
) -> tuple[str, int]:
    cursor = offset + 1
    start = cursor
    depth = 1
    escaped = False
    while cursor < len(text):
        character = text[cursor]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif nested and character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return text[start:cursor], cursor + 1
        cursor += 1
    raise ValueError("Ein BibTeX-Feldwert wurde nicht abgeschlossen.")


def _bibtex_top_level_delimiter(text: str, delimiter: str) -> int:
    depth = 0
    quoted = False
    escaped = False
    for cursor, character in enumerate(text):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"' and depth == 0:
            quoted = not quoted
        elif not quoted:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            elif character == delimiter and depth == 0:
                return cursor
    return -1


def _bibtex_unescape(value: str) -> str:
    value = value.replace(r"\textbackslash{}", "\\")
    value = re.sub(r"\\([{}$%&#_])", r"\1", value)
    return " ".join(value.replace("\x00", "").split())


def _bibtex_row(fields: dict[str, str]) -> dict[str, str]:
    title = fields.get("title", "")
    if not title:
        raise ValueError("Ein BibTeX-Buchdatensatz benötigt einen Titel.")
    note_values = _parse_bibtex_note(fields.get("note", ""))
    isbn = fields.get("isbn", "") or note_values.get("isbn", "")
    if len(isbn) > 64:
        raise ValueError("Die ISBN im BibTeX-Datensatz ist zu lang.")
    return {
        "title": title,
        "authors": re.sub(r"\s+and\s+", "; ", fields.get("author", ""), flags=re.IGNORECASE),
        "isbn": isbn,
        "genre": fields.get("keywords", "") or note_values.get("genre", ""),
        "series": fields.get("series", ""),
        "series_number": fields.get("volume", ""),
        "publisher": fields.get("publisher", ""),
        "year": fields.get("year", ""),
        "language": fields.get("language", "") or note_values.get("language", ""),
        "edition": fields.get("edition", ""),
        "description": fields.get("abstract", "") or note_values.get("description", ""),
        "bibliography_note": note_values.get("note", ""),
    }


def _parse_bibtex_note(note: str) -> dict[str, str]:
    if not note:
        return {}
    labels = "Inventory ID|Library location|Pages|ISBN|Language|Genre|Description"
    parts = re.split(rf";\s+(?=(?:{labels}):)", note)
    values: dict[str, str] = {}
    local_parts: list[str] = []
    for part in parts:
        match = re.fullmatch(rf"({labels}):\s*(.*)", part)
        if not match:
            local_parts.append(part)
            continue
        key = match.group(1).casefold().replace(" ", "_")
        values[key] = match.group(2)
        if key in {"inventory_id", "library_location", "pages"}:
            local_parts.append(part)
    if local_parts:
        values["note"] = "; ".join(local_parts)
    return values
