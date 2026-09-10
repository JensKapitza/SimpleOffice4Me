"""Safe application of reviewed data-roulette proposals.

Roulette answers are proposals first.  This module applies only explicitly
reviewed proposals to the underlying source data and never broadens access.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contact_store import ContactStore
from .document_store import DocumentStore
from .gamification_adapters import CONTACT_GAME_FIELDS, contact_candidates, image_candidates
from .gamification_document_selection import document_candidate_from_object_ref

MAX_TAGS_PER_PROPOSAL = 50
MAX_TAG_LENGTH = 100
MAX_NOTE_LENGTH = 500


@dataclass(frozen=True)
class ProposalConflict(ValueError):
    field_name: str
    current_value: str
    proposed_value: str

    def __str__(self) -> str:
        return "Bestehender Kontaktwert weicht vom Vorschlag ab"


def _document_id(object_ref: str) -> str:
    value = str(object_ref).strip()
    if not value.startswith("document:"):
        raise ValueError("Ungültiges Dokumentziel")
    document_id = value.removeprefix("document:").strip()
    if not document_id or len(document_id) > 200:
        raise ValueError("Ungültiges Dokumentziel")
    return document_id


def _contact_id(object_ref: str) -> str:
    value = str(object_ref).strip()
    if not value.startswith("contact:"):
        raise ValueError("Ungültiges Kontaktziel")
    contact_id = value.removeprefix("contact:").strip()
    if not contact_id or len(contact_id) > 200:
        raise ValueError("Ungültiges Kontaktziel")
    return contact_id


def _proposal_text(value: Any, *, limit: int = MAX_NOTE_LENGTH) -> str:
    if not isinstance(value, str):
        raise ValueError("Vorschlag muss Text enthalten")
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ValueError("Vorschlag ist leer oder zu lang")
    return normalized


def _proposal_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        raise ValueError("Tag-Vorschlag hat ein ungültiges Format")

    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = str(item).strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError("Ein vorgeschlagener Tag ist zu lang")
        key = tag.casefold()
        if key in seen:
            continue
        result.append(tag)
        seen.add(key)
        if len(result) > MAX_TAGS_PER_PROPOSAL:
            raise ValueError("Zu viele Tags in einem Vorschlag")
    if not result:
        raise ValueError("Tag-Vorschlag ist leer")
    return result


def _contact_is_still_safe(root: str | Path, actor: str, object_ref: str) -> bool:
    return any(candidate.object_ref == object_ref for candidate in contact_candidates(root, actor))


def _image_is_still_safe(root: str | Path, actor: str, object_ref: str) -> bool:
    return any(candidate.object_ref == object_ref for candidate in image_candidates(root, actor))


def apply_contact_answer(
    root: str | Path,
    actor: str,
    proposal: dict[str, Any],
    *,
    replace_existing: bool = False,
) -> str:
    """Apply one reviewed contact answer, requiring confirmation on conflicts."""
    if proposal.get("provider") != "contacts" or proposal.get("accepted_by"):
        raise ValueError("Kontaktvorschlag ist nicht anwendbar")
    field_name = str(proposal.get("field_name", "")).strip()
    if field_name not in CONTACT_GAME_FIELDS:
        raise ValueError("Kontaktfeld ist für Daten-Roulette nicht freigegeben")
    proposed = _proposal_text(proposal.get("value"))
    object_ref = str(proposal.get("object_ref", "")).strip()
    if not _contact_is_still_safe(root, actor, object_ref):
        raise ValueError("Kontakt ist nicht mehr für Daten-Roulette geeignet")

    contact_id = _contact_id(object_ref)
    store = ContactStore(root)
    contact = store.get(contact_id, actor)
    if not store.can_manage_contact(contact, actor):
        raise ValueError("Schreibrecht für den Kontakt fehlt")
    current = str(contact.get("fields", {}).get(field_name, "")).strip()
    if current == proposed:
        return "Kontaktwert war bereits identisch."
    if current and not replace_existing:
        raise ProposalConflict(field_name, current, proposed)

    store.patch_fields(contact_id, {field_name: proposed}, actor)
    return "Kontaktvorschlag übernommen."


def apply_document_answer(root: str | Path, actor: str, proposal: dict[str, Any]) -> str:
    """Apply reviewed tags or notes to a document/image without exposing content."""
    provider = str(proposal.get("provider", "")).strip()
    if provider not in {"documents", "images"} or proposal.get("accepted_by"):
        raise ValueError("Dateivorschlag ist nicht anwendbar")
    object_ref = str(proposal.get("object_ref", "")).strip()
    if provider == "documents":
        safe = document_candidate_from_object_ref(root, actor, object_ref, require_release=False)
        if safe is None:
            raise ValueError("Datei ist nicht mehr für diese Runde geeignet")
    elif not _image_is_still_safe(root, actor, object_ref):
        raise ValueError("Bild ist nicht mehr für diese Runde geeignet")

    document_id = _document_id(object_ref)
    store = DocumentStore(root)
    field_name = str(proposal.get("field_name", "")).strip()

    if field_name == "tags":
        proposed_tags = _proposal_tags(proposal.get("value"))
        document = store.get_document(document_id)
        existing = [str(tag).strip() for tag in document.get("tags", []) if str(tag).strip()]
        seen = {tag.casefold() for tag in existing}
        additions = [tag for tag in proposed_tags if tag.casefold() not in seen]
        if additions:
            store.set_tags(document_id, [*existing, *additions], actor)
            return f"{len(additions)} Tag(s) übernommen."
        return "Alle vorgeschlagenen Tags waren bereits vorhanden."

    if provider == "documents" and field_name == "note":
        note = _proposal_text(proposal.get("value"), limit=MAX_NOTE_LENGTH)
        store.add_note(document_id, note, actor)
        return "Notiz aus dem Roulette-Vorschlag übernommen."

    raise ValueError("Dieser Vorschlag kann nur als Annotation bestätigt werden")


def apply_supported_proposal(
    root: str | Path,
    actor: str,
    proposal: dict[str, Any],
    *,
    replace_existing: bool = False,
) -> tuple[bool, str]:
    """Return ``(applied_to_source, message)`` for one reviewed proposal."""
    provider = str(proposal.get("provider", "")).strip()
    field_name = str(proposal.get("field_name", "")).strip()
    if provider == "contacts":
        return True, apply_contact_answer(root, actor, proposal, replace_existing=replace_existing)
    if provider in {"documents", "images"} and field_name == "tags":
        return True, apply_document_answer(root, actor, proposal)
    if provider == "documents" and field_name == "note":
        return True, apply_document_answer(root, actor, proposal)
    return False, "Vorschlag als bestätigte Annotation gespeichert."
