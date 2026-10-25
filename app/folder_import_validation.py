"""Validate a multipart folder-import batch before creating storage objects."""
from __future__ import annotations

import json
import re
import unicodedata
from uuid import NAMESPACE_URL, uuid5

_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)
_SOURCE_ID = re.compile(r"[0-9a-f]{64}")
MAX_RELATIVE_PATH_MANIFEST_BYTES = 400_000
MAX_SOURCE_ID_MANIFEST_BYTES = 100_000
MAX_FOLDER_IMPORT_DEPTH = 64


def stable_source_folder_id(actor: str, source_identity: str, source_path: str) -> str:
    """Derive a per-user Smart View ID without persisting a raw device identifier."""
    if (
        not isinstance(actor, str) or not actor
        or not isinstance(source_identity, str) or not _SOURCE_ID.fullmatch(source_identity)
        or not isinstance(source_path, str) or not source_path
    ):
        raise ValueError("Ungültige Herkunftsidentität")
    namespace = uuid5(
        NAMESPACE_URL,
        f"simpleoffice4me-folder-origin:{actor}:{source_identity}",
    )
    return uuid5(namespace, source_path).hex


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


def decode_source_identity_manifest(value: object, expected_count: int) -> tuple[list[str] | None, str | None]:
    """Decode optional opaque folder identities aligned with uploaded files."""
    if value in (None, ""):
        return [""] * expected_count, None
    if not isinstance(value, str):
        return None, "Ordnerimport unvollständig: Herkunfts-IDs sind ungültig."
    try:
        if len(value.encode("utf-8")) > MAX_SOURCE_ID_MANIFEST_BYTES:
            return None, "Herkunfts-ID-Liste ist zu groß. Bitte einen kleineren Ordnerstapel auswählen."
        identities = json.loads(value)
    except (UnicodeEncodeError, json.JSONDecodeError):
        return None, "Ordnerimport unvollständig: Herkunfts-IDs sind ungültig."
    if (
        not isinstance(identities, list)
        or len(identities) != expected_count
        or any(
            not isinstance(identity, str)
            or identity and not _SOURCE_ID.fullmatch(identity)
            for identity in identities
        )
    ):
        return None, "Ordnerimport unvollständig: Herkunfts-IDs fehlen oder sind ungültig."
    return identities, None


def validate_source_identity_groups(relative_paths: list[str], source_ids: list[str]) -> str | None:
    """A directory root must not carry conflicting identities in one batch."""
    if len(relative_paths) != len(source_ids):
        return "Ordnerimport unvollständig: Herkunfts-IDs passen nicht zu den Dateipfaden."
    identities_by_root: dict[str, str] = {}
    for path, source_id in zip(relative_paths, source_ids):
        root_name = path.replace(chr(92), "/").split("/", 1)[0].casefold()
        if root_name in identities_by_root and identities_by_root[root_name] != source_id:
            return "Eine Ordnerquelle enthält widersprüchliche Herkunfts-IDs."
        identities_by_root[root_name] = source_id
    return None


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
        if len(segments) > MAX_FOLDER_IMPORT_DEPTH + 2:
            return "Die Ordnerstruktur ist zu tief verschachtelt."
        if relative.startswith("/") or any(_invalid_segment(segment) for segment in segments):
            return "Ungültiger relativer Ordnerpfad."
        if segments[-1] != filename.rsplit("/", 1)[-1]:
            return "Ungültiger relativer Ordnerpfad."
        key = relative.casefold()
        if key in seen:
            return "Doppelter Dateipfad im Ordnerimport."
        seen.add(key)
    return None
