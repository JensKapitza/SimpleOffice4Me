"""Adapters from application stores into fail-closed gamification candidates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contact_store import ContactStore
from .document_store import DocumentStore
from .gamification_engine import Candidate
from .gamification_policy import is_hard_excluded
from .preview_service import IMAGE_SUFFIXES, PreviewService


# These fields are business/financial/CRM metadata and make a contact unsuitable
# for the generic data-quality game. The game is deliberately stricter than the
# normal contact UI: a readable contact is not automatically a playable contact.
CONTACT_GAME_BLOCKING_FIELDS = frozenset({
    "customer_number", "supplier_number", "discount", "payment_terms",
    "payment_days", "currency", "vat_id", "tax_number", "bank_iban",
    "bank_bic", "crm_id", "crm_status", "billing_address", "payment_method",
})

CONTACT_GAME_FIELDS = (
    "street", "house_number", "postal_code", "city", "country",
    "phone", "mobile", "email", "company",
)

PHOTO_GAME_BLOCKING_MARKERS = frozenset({
    "private", "privat", "vertraulich", "intern", "confidential",
})

DOCUMENT_GAME_RELEASE_TAGS = frozenset({
    "gamification", "gamification-freigegeben", "daten-roulette", "spiel-freigabe",
})
DOCUMENT_GAME_BLOCKING_MARKERS = PHOTO_GAME_BLOCKING_MARKERS


def _has_blocking_contact_data(contact: dict[str, Any]) -> bool:
    fields = contact.get("fields", {})
    if not isinstance(fields, dict):
        return True
    for key in CONTACT_GAME_BLOCKING_FIELDS:
        if str(fields.get(key, "")).strip():
            return True
    markers = {
        str(value).strip().casefold()
        for value in [*contact.get("tags", []), *contact.get("groups", [])]
        if str(value).strip()
    }
    blocked_markers = {
        "crm", "buchhaltung", "billing", "rechnung", "rechnungen",
        "payment", "zahlung", "bank", "intern", "vertraulich", "private",
    }
    return bool(markers & blocked_markers)


def contact_candidates(root: str | Path, actor: str) -> list[Candidate]:
    """Return only contacts already readable by actor and safe for the game."""
    if not str(actor).strip():
        return []
    store = ContactStore(root)
    result: list[Candidate] = []
    for contact in store.contacts(actor):
        if _has_blocking_contact_data(contact):
            continue
        fields = contact.get("fields", {})
        if not isinstance(fields, dict):
            continue
        contact_id = str(contact.get("contact_id", "")).strip()
        display_name = str(fields.get("display_name", "")).strip()
        if not contact_id or not display_name:
            continue
        existing_fields = tuple(
            field for field in CONTACT_GAME_FIELDS if str(fields.get(field, "")).strip()
        )
        result.append(Candidate(
            provider="contacts",
            object_ref=f"contact:{contact_id}",
            data={"display_name": display_name[:200], "existing_fields": existing_fields},
            normal_read_allowed=True,
            resource_class="contact",
            collection="contacts",
        ))
    return result


def _document_markers(document: dict[str, Any]) -> list[str] | None:
    tags = document.get("tags", [])
    if not isinstance(tags, list):
        return None
    values: list[str] = [str(value) for value in tags if str(value).strip()]
    for key in ("resource_class", "classification", "category", "document_type", "doctype", "state"):
        value = document.get(key)
        if value not in (None, ""):
            values.append(str(value))
    return values


def _document_is_blocked(document: dict[str, Any], extra_blocked: frozenset[str]) -> bool:
    values = _document_markers(document)
    if values is None or is_hard_excluded(*values):
        return True
    normalized = {str(value).strip().casefold() for value in values if str(value).strip()}
    return bool(normalized & extra_blocked)


def _safe_photo_document(root: str | Path, document: dict[str, Any]) -> Path | None:
    """Return the cached thumbnail for one explicitly uploaded safe photo.

    The original is deliberately never returned. Missing or stale preview
    caches make a photo ineligible until the normal preview worker created one.
    """
    document_id = str(document.get("document_id", "")).strip()
    last_path = str(document.get("last_path", "")).strip()
    attributes = document.get("attributes", {})
    if not document_id or not last_path or not isinstance(attributes, dict):
        return None
    upload = attributes.get("photo_upload")
    if not isinstance(upload, dict) or str(upload.get("source", "")).strip() != "mobile-web-bulk":
        return None
    if Path(last_path).suffix.casefold() not in IMAGE_SUFFIXES or _document_is_blocked(document, PHOTO_GAME_BLOCKING_MARKERS):
        return None
    return PreviewService(root).cached_path(document, "thumbnail")


def image_candidates(root: str | Path, actor: str) -> list[Candidate]:
    """Expose only explicit photo uploads with an already generated safe preview."""
    if not str(actor).strip():
        return []
    store = DocumentStore(root)
    result: list[Candidate] = []
    for document in store.list_documents():
        if _safe_photo_document(root, document) is None:
            continue
        document_id = str(document.get("document_id", "")).strip()
        if not document_id:
            continue
        result.append(Candidate(
            provider="images",
            object_ref=f"document:{document_id}",
            data={"preview": True},
            normal_read_allowed=True,
            resource_class="photo",
            collection="images",
        ))
    return result


def image_preview_path(root: str | Path, actor: str, object_ref: str) -> Path | None:
    """Resolve a game image to its cached thumbnail after re-checking eligibility."""
    if not str(actor).strip() or not str(object_ref).startswith("document:"):
        return None
    document_id = str(object_ref).removeprefix("document:").strip()
    if not document_id or len(document_id) > 200:
        return None
    store = DocumentStore(root)
    try:
        document = store.get_document(document_id)
    except ValueError:
        return None
    return _safe_photo_document(root, document)


def document_candidates(root: str | Path, actor: str) -> list[Candidate]:
    """Return explicitly released, non-sensitive files for metadata guessing.

    Release is positive, not inferred: one of DOCUMENT_GAME_RELEASE_TAGS must be
    present. Full paths, contents, OCR text, notes, attributes and current tags
    are never copied into the challenge. Images use their stricter photo adapter.
    """
    if not str(actor).strip():
        return []
    store = DocumentStore(root)
    result: list[Candidate] = []
    for document in store.list_documents():
        document_id = str(document.get("document_id", "")).strip()
        last_path = str(document.get("last_path", "")).strip()
        tags = document.get("tags", [])
        attributes = document.get("attributes", {})
        if not document_id or not last_path or not isinstance(tags, list) or not isinstance(attributes, dict):
            continue
        normalized_tags = {str(value).strip().casefold() for value in tags if str(value).strip()}
        if not (normalized_tags & DOCUMENT_GAME_RELEASE_TAGS):
            continue
        if _document_is_blocked(document, DOCUMENT_GAME_BLOCKING_MARKERS):
            continue
        if Path(last_path).suffix.casefold() in IMAGE_SUFFIXES or isinstance(attributes.get("photo_upload"), dict):
            continue
        display_name = Path(last_path).name.strip()
        if not display_name:
            continue
        result.append(Candidate(
            provider="documents",
            object_ref=f"document:{document_id}",
            data={"display_name": display_name[:200]},
            normal_read_allowed=True,
            resource_class="released_file",
            collection="files",
        ))
    return result


def contact_proposal_can_apply(root: str | Path, actor: str, proposal: dict[str, Any]) -> bool:
    """Return whether a proposal is still eligible for explicit manual adoption."""
    if proposal.get("provider") != "contacts" or proposal.get("accepted_by"):
        return False
    object_ref = str(proposal.get("object_ref", ""))
    field_name = str(proposal.get("field_name", ""))
    value = proposal.get("value")
    if not object_ref.startswith("contact:") or field_name not in CONTACT_GAME_FIELDS:
        return False
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 500:
        return False
    contact_id = object_ref.removeprefix("contact:").strip()
    if not contact_id:
        return False
    store = ContactStore(root)
    try:
        contact = store.get(contact_id, actor)
    except ValueError:
        return False
    if _has_blocking_contact_data(contact) or not store.can_manage_contact(contact, actor):
        return False
    current = str(contact.get("fields", {}).get(field_name, "")).strip()
    return not current or current == value.strip()


def apply_contact_proposal(root: str | Path, actor: str, proposal: dict[str, Any]) -> dict[str, Any]:
    """Apply a reviewed contact proposal without extending normal write rights."""
    if proposal.get("provider") != "contacts":
        raise ValueError("proposal provider is not supported")
    if proposal.get("accepted_by"):
        raise ValueError("proposal was already accepted")
    object_ref = str(proposal.get("object_ref", ""))
    field_name = str(proposal.get("field_name", ""))
    value = proposal.get("value")
    if not object_ref.startswith("contact:") or field_name not in CONTACT_GAME_FIELDS:
        raise ValueError("proposal target is not allowed")
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 500:
        raise ValueError("proposal value is invalid")
    contact_id = object_ref.removeprefix("contact:").strip()
    if not contact_id:
        raise ValueError("proposal contact is invalid")

    store = ContactStore(root)
    contact = store.get(contact_id, actor)
    if _has_blocking_contact_data(contact):
        raise ValueError("contact is excluded from gamification")
    if not store.can_manage_contact(contact, actor):
        raise ValueError("contact write permission is required")

    current = str(contact.get("fields", {}).get(field_name, "")).strip()
    normalized = value.strip()
    if current == normalized:
        return contact
    if current:
        raise ValueError("contact field changed after proposal creation")

    return store.patch_fields(contact_id, {field_name: normalized}, actor)
