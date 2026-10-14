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
        return
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
