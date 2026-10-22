"""Validate a multipart folder-import batch before creating storage objects."""
from __future__ import annotations

import json
import re
import unicodedata

_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)
MAX_RELATIVE_PATH_MANIFEST_BYTES = 400_000


def decode_relative_path_manifest(value: object, expected_count: int) -> tuple[list[str] | None, str | None]:
    """Decode one bounded form field instead of one multipart field per path."""
    if not isinstance(value, str):
        return None, "Ordnerimport unvollständig: Dateipfade fehlen."
    try:
        if len(value.encode("utf-8")) > MAX_RELATIVE_PATH_MANIFEST_BYTES:
            return None, "Pfadliste ist zu groß. Bitte einen kleineren Ordnerstapel auswählen."
        paths = json.loads(value)
    except (UnicodeEncodeError, json.JSONDecodeError):
        return None, "Ordnerimport unvollständig: Dateipfade sind ungültig."
    if (
        not isinstance(paths, list)
        or len(paths) != expected_count
        or any(not isinstance(path, str) for path in paths)
    ):
        return None, "Ordnerimport unvollständig: Dateipfade fehlen."
    return paths, None


def _invalid_segment(segment: str) -> bool:
    try:
        encoded_size = len(segment.encode("utf-8"))
    except UnicodeEncodeError:
        return True
    return (
        not segment
        or segment in (".", "..")
        or segment.rstrip(" .") != segment
        or encoded_size > 255
        or ":" in segment
        or len(segment) > 255
        or any(ord(char) < 32 for char in segment)
        or unicodedata.normalize("NFC", segment) != segment
        or bool(_RESERVED.fullmatch(segment))
    )


def validate_upload_batch(files, relative_paths, *, folder_import: bool) -> str | None:
    if folder_import and any(not isinstance(path, str) or len(path) > 4096 for path in relative_paths):
        return "Relativer Ordnerpfad ist ungültig oder zu lang."
    if len(files) > 1000:
        return "Maximal 1000 Dateien pro Importvorgang auswählen."
    if folder_import and len(relative_paths) != len(files):
        return "Ordnerimport unvollständig: Dateipfade fehlen."
    seen = set()
    for index, item in enumerate(files):
        filename = item.filename.replace(chr(92), "/")
        if filename.startswith("/") or any(
            _invalid_segment(segment) for segment in filename.split("/")
        ):
            return "Ungültiger Dateiname im Import."
        if not folder_import:
            continue
        relative = relative_paths[index].replace(chr(92), "/")
        try:
            relative_size = len(relative.encode("utf-8"))
        except UnicodeEncodeError:
            return "Ungültiger relativer Ordnerpfad."
        if relative_size > 4096:
            return "Relativer Ordnerpfad ist zu lang."
        segments = relative.split("/")
        if relative.startswith("/") or any(_invalid_segment(segment) for segment in segments):
            return "Ungültiger relativer Ordnerpfad."
        if segments[-1] != filename.rsplit("/", 1)[-1]:
            return "Ungültiger relativer Ordnerpfad."
        key = relative.casefold()
        if key in seen:
            return "Doppelter Dateipfad im Ordnerimport."
        seen.add(key)
    return None
