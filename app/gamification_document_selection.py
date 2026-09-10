"""Explicit document opt-in for data-roulette rounds.

A document can be made available in two ways:

* persistently by adding one of the existing data-roulette release tags;
* temporarily for exactly one game session by resolving a normal document
  search query when the session is created.

The query path deliberately does not modify document tags.  It still applies
all hard exclusions used by the regular document adapter.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .document_store import DocumentStore
from .gamification_adapters import DOCUMENT_GAME_BLOCKING_MARKERS, DOCUMENT_GAME_RELEASE_TAGS
from .gamification_engine import Candidate
from .gamification_policy import is_hard_excluded
from .preview_service import IMAGE_SUFFIXES

CANONICAL_DOCUMENT_GAME_RELEASE_TAG = "daten-roulette"
MAX_ROUND_QUERY_LENGTH = 1000
MAX_ROUND_DOCUMENTS = 100


def _markers(document: dict[str, Any]) -> list[str] | None:
    tags = document.get("tags", [])
    if not isinstance(tags, list):
        return None
    values = [str(value) for value in tags if str(value).strip()]
    for key in ("resource_class", "classification", "category", "document_type", "doctype", "state"):
        value = document.get(key)
        if value not in (None, ""):
            values.append(str(value))
    return values


def _safe_document(document: dict[str, Any], *, require_release: bool) -> bool:
    document_id = str(document.get("document_id", "")).strip()
    last_path = str(document.get("last_path", "")).strip()
    tags = document.get("tags", [])
    attributes = document.get("attributes", {})
    if not document_id or not last_path or not isinstance(tags, list) or not isinstance(attributes, dict):
        return False

    markers = _markers(document)
    if markers is None or is_hard_excluded(*markers):
        return False
    normalized = {str(value).strip().casefold() for value in markers if str(value).strip()}
    if normalized & DOCUMENT_GAME_BLOCKING_MARKERS:
        return False

    normalized_tags = {str(value).strip().casefold() for value in tags if str(value).strip()}
    if require_release and not normalized_tags.intersection(DOCUMENT_GAME_RELEASE_TAGS):
        return False

    # Images remain owned by the preview-only image provider.  A document search
    # must never turn an image into an original-file disclosure path.
    if Path(last_path).suffix.casefold() in IMAGE_SUFFIXES or isinstance(attributes.get("photo_upload"), dict):
        return False
    return bool(Path(last_path).name.strip())


def document_candidate(
    root: str | Path,
    actor: str,
    document_id: str,
    *,
    require_release: bool = True,
) -> Candidate | None:
    """Return one safe document candidate without exposing file contents."""
    if not str(actor).strip() or not str(document_id).strip():
        return None
    store = DocumentStore(root)
    try:
        document = store.get_document(str(document_id).strip())
    except ValueError:
        return None
    if not _safe_document(document, require_release=require_release):
        return None
    return Candidate(
        provider="documents",
        object_ref=f"document:{document['document_id']}",
        data={"display_name": Path(str(document["last_path"])).name.strip()[:200]},
        normal_read_allowed=True,
        resource_class="released_file",
        collection="files",
    )


def document_candidate_from_object_ref(
    root: str | Path,
    actor: str,
    object_ref: str,
    *,
    require_release: bool = True,
) -> Candidate | None:
    prefix = "document:"
    value = str(object_ref).strip()
    if not value.startswith(prefix):
        return None
    return document_candidate(root, actor, value.removeprefix(prefix), require_release=require_release)


def query_document_candidates(
    root: str | Path,
    actor: str,
    query: str,
    *,
    limit: int = MAX_ROUND_DOCUMENTS,
) -> list[Candidate]:
    """Resolve the normal document-search syntax as a session-only opt-in."""
    normalized = str(query).strip()
    if not normalized:
        return []
    if len(normalized) > MAX_ROUND_QUERY_LENGTH:
        raise ValueError("Roulette-Suchausdruck ist zu lang")
    maximum = max(1, min(int(limit), MAX_ROUND_DOCUMENTS))
    store = DocumentStore(root)
    matches = store.search(normalized, limit=maximum)
    result: list[Candidate] = []
    seen: set[str] = set()
    for match in matches:
        document_id = str(match.get("document_id", "")).strip()
        if not document_id or document_id in seen:
            continue
        candidate = document_candidate(root, actor, document_id, require_release=False)
        if candidate is None:
            continue
        result.append(candidate)
        seen.add(document_id)
    return result


def set_document_game_release(root: str | Path, actor: str, document_id: str, released: bool) -> dict[str, Any]:
    """Toggle the persistent roulette tag while preserving all unrelated tags."""
    store = DocumentStore(root)
    document = store.get_document(document_id)
    tags = [str(value).strip() for value in document.get("tags", []) if str(value).strip()]

    if released:
        if document_candidate(root, actor, document_id, require_release=False) is None:
            raise ValueError("Diese Datei ist für Daten-Roulette gesperrt oder ungeeignet")
        if not any(tag.casefold() in DOCUMENT_GAME_RELEASE_TAGS for tag in tags):
            tags.append(CANONICAL_DOCUMENT_GAME_RELEASE_TAG)
    else:
        tags = [tag for tag in tags if tag.casefold() not in DOCUMENT_GAME_RELEASE_TAGS]

    return store.set_tags(document_id, tags, actor)
