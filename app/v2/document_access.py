"""Projection-free document content access for V2-aware consumers.

Metadata remains available through the existing document read model while payload
bytes are always read through StoragePort. Consumers that require a filesystem
path receive a verified, private, short-lived materialization.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..document_store import DocumentStore
from .catalog import CatalogState, ObjectCatalog
from .contracts import LogicalObjectId
from .materialize import materialize_verified_object
from .storage_runtime import result_or_raise, storage_for


def document_metadata(root: str | Path, document_id: str) -> dict:
    """Return the metadata read model for one managed document."""
    return DocumentStore(root).get_document(str(document_id))


def document_bytes(root: str | Path, actor: str, document_id: str) -> bytes:
    """Read verified authoritative bytes for one managed document."""
    return result_or_raise(
        storage_for(root, actor).read_bytes(LogicalObjectId(str(document_id)))
    )


def document_size(root: str | Path, actor: str, document_id: str) -> int:
    """Return authoritative size without requiring a persistent plaintext file."""
    entry = ObjectCatalog(root).get(LogicalObjectId(str(document_id)))
    if entry.ok and entry.value.state is CatalogState.ACTIVE:
        return int(entry.value.size)
    return len(document_bytes(root, actor, document_id))


def document_sha256(root: str | Path, actor: str, document_id: str) -> str:
    """Return a trustworthy SHA-256 for the current authoritative content."""
    metadata = document_metadata(root, document_id)
    digest = str(metadata.get("sha256") or "").strip().casefold()
    if len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest):
        return digest
    return hashlib.sha256(document_bytes(root, actor, document_id)).hexdigest()


def document_id_for_sha256(root: str | Path, digest: str) -> str:
    """Resolve one active V2 object by its current content digest."""
    normalized = str(digest or "").strip().casefold()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("invalid SHA-256 digest")
    matches = [
        entry.object_id.value
        for entry in ObjectCatalog(root).list()
        if entry.state is CatalogState.ACTIVE and entry.content_sha256 == normalized
    ]
    if not matches:
        raise ValueError("document content is not available in the V2 catalog")
    return sorted(matches)[0]


@contextmanager
def materialized_blob(
    root: str | Path,
    actor: str,
    digest: str,
    *,
    suffix: str = "",
) -> Iterator[Path]:
    """Yield one verified temporary file selected by current content digest."""
    document_id = document_id_for_sha256(root, digest)
    with materialize_verified_object(root, actor, document_id, suffix=suffix) as path:
        yield path


@contextmanager
def materialized_document(
    root: str | Path,
    actor: str,
    document_id: str,
    *,
    suffix: str | None = None,
) -> Iterator[Path]:
    """Yield a verified temporary file for path-only libraries/tools."""
    metadata = document_metadata(root, document_id)
    extension = (
        Path(str(metadata.get("last_path") or "")).suffix.lower()
        if suffix is None
        else suffix
    )
    with materialize_verified_object(
        root,
        actor,
        str(document_id),
        suffix=extension,
    ) as path:
        yield path
