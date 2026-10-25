"""Safe idempotency checks for replayed folder-import files."""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import BinaryIO
from uuid import UUID, uuid5

from .safe_paths import resolve_file_under


def existing_matching_folder_import(
    store,
    document_root: str,
    relative_path: str,
    source_relative_path: str,
    import_id: str,
    actor: str,
    source_identity: str,
    content: BinaryIO,
    logger,
) -> dict | None:
    """Return existing metadata only when a replay matches the committed file.

    The exact actor-scoped import destination and matching SHA-256 bind this
    replay to the bytes already stored. Both stored and incoming content are
    hashed before the route treats an ambiguous upload as complete.
    """
    try:
        metadata = store.get_document(relative_path)
        if metadata.get("last_path") != relative_path:
            return None
        stored_path = resolve_file_under(document_root, relative_path)
        with stored_path.open("rb") as stored:
            stored_digest = _digest(stored)
    except (OSError, ValueError, AttributeError):
        return None
    incoming_position = content.tell()
    try:
        content.seek(0)
        incoming_digest = _digest(content)
    finally:
        content.seek(incoming_position)
    if incoming_digest != stored_digest or metadata.get("sha256") != stored_digest:
        return None
    source_origin = folder_import_origin(import_id, source_relative_path)
    if source_origin:
        attributes = metadata.get("attributes")
        origin = attributes.get("folder_import_origin") if isinstance(attributes, dict) else None
        if isinstance(origin, dict):
            if (
                origin.get("import_id") != import_id
                or origin.get("source_path") != source_origin["source_path"]
                or origin.get("source_identity", "") != source_identity
            ):
                return None
        if origin != source_origin:
            source_origin = folder_import_origin(
                import_id, source_relative_path, actor, source_identity,
            )
            try:
                store.update_metadata(
                    metadata["document_id"],
                    attributes={"folder_import_origin": source_origin},
                    author=actor,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "Folder import replay provenance could not be stored for document %s (%s)",
                    metadata.get("document_id", "unknown"),
                    type(exc).__name__,
                )
    return metadata


def folder_import_origin(
    import_id: str, relative_path: str, actor: str = "", source_identity: str = "",
) -> dict | None:
    """Build stable provenance metadata for a validated source-relative path."""
    source = PurePosixPath(relative_path).parent
    if source.as_posix() == ".":
        return None
    source_path = source.as_posix()
    result = {
        "import_id": import_id,
        "folder_id": uuid5(UUID(hex=import_id), source_path).hex,
        "source_path": source_path,
        "source_name": source.name,
    }
    if source_identity:
        from .folder_import_validation import stable_source_folder_id

        result["source_identity"] = source_identity
        result["folder_id"] = stable_source_folder_id(actor, source_identity, source_path)
    return result


def _digest(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()
