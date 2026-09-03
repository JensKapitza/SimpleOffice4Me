"""Canonical, display-safe document provenance tags."""
from __future__ import annotations

from pathlib import Path
from typing import Any


ORIGIN_ATTRIBUTE_KEYS = {
    "attachment_origin",
    "copied_from",
    "email_origin",
    "federation_origin",
    "import_origin",
    "mail_origin",
    "source",
    "webdav_origin",
}


def _clean(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ").replace("\r", " ")
    return text[:limit]


def document_origin_tags(document: dict[str, Any]) -> list[str]:
    """Derive stable provenance tags without exposing secrets or full URLs."""
    tags = {str(tag).strip() for tag in document.get("tags", []) if str(tag).strip()}
    attributes = document.get("attributes") if isinstance(document.get("attributes"), dict) else {}
    if not isinstance(attributes, dict):
        attributes = {}
    if "email_origin" in attributes or "mail_origin" in attributes:
        tags.update({"origin:email", "source:imap"})
    if "attachment_origin" in attributes:
        tags.update({"origin:attachment", "source:eml"})
    if "webdav_origin" in attributes:
        tags.add("source:webdav")
    if "import_origin" in attributes:
        tags.add("origin:import")
    if "copied_from" in attributes:
        tags.add("origin:copy")
    federation = attributes.get("federation_origin")
    if isinstance(federation, dict):
        tags.add("source:federation")
        peer = _clean(federation.get("peer_id"))
        remote_document_id = _clean(federation.get("remote_document_id"))
        origin_peer = _clean(federation.get("origin_peer"))
        if peer:
            tags.add(f"federation-peer:{peer}")
        if origin_peer:
            tags.add(f"federation-origin:{origin_peer}")
        if remote_document_id:
            tags.add(f"federation-document:{remote_document_id}")
    generic = attributes.get("source")
    if isinstance(generic, str) and generic.strip():
        tags.add(f"source:{_clean(generic).casefold()}")
    return sorted(tags, key=str.casefold)


def provenance_summary(document: dict[str, Any]) -> dict[str, Any]:
    attributes = document.get("attributes") if isinstance(document.get("attributes"), dict) else {}
    return {
        "origin_tags": document_origin_tags(document),
        "origins": {
            key: attributes[key]
            for key in ORIGIN_ATTRIBUTE_KEYS
            if key in attributes
        },
    }


def persist_origin_tags(root: str | Path, actor: str = "system") -> dict[str, int]:
    """Backfill derived origin tags into normal document tags.

    Persisting them makes provenance visible in every existing document view and
    search that already renders or indexes ordinary tags, without introducing a
    second UI-specific provenance field.
    """
    from .document_store import DocumentStore

    store = DocumentStore(root)
    scanned = changed = errors = 0
    for document in store.list_documents():
        scanned += 1
        try:
            current = sorted({str(tag).strip() for tag in document.get("tags", []) if str(tag).strip()}, key=str.casefold)
            desired = document_origin_tags(document)
            if desired == current:
                continue
            store.update_metadata(document["document_id"], tags=desired, author=actor)
            changed += 1
        except (OSError, ValueError):
            errors += 1
    return {"scanned": scanned, "changed": changed, "errors": errors}
