"""Validate a multipart folder-import batch before creating storage objects."""
from __future__ import annotations

import re
import unicodedata

_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)


def _invalid_segment(segment: str) -> bool:
    return (
        not segment
        or segment in (".", "..")
        or segment.rstrip(" .") != segment
        or len(segment.encode("utf-8")) > 255
        or ":" in segment
        or len(segment) > 255
        or any(ord(char) < 32 for char in segment)
        or unicodedata.normalize("NFC", segment) != segment
        or bool(_RESERVED.fullmatch(segment))
    )


def validate_upload_batch(files, relative_paths, *, folder_import: bool) -> str | None:
    if folder_import and any(len(path) > 4096 for path in relative_paths):
        return "Relativer Ordnerpfad ist zu lang."
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
        if len(relative.encode("utf-8")) > 4096:
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
