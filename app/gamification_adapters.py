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
# for the generic data-quality game.  The game is deliberately stricter than the
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


def _has_blocking_contact_data(contact: dict[str, Any]) -> bool:
    fields = contact.get("fields", {})
    if not isinstance(fields, dict):
        return True
    for key in CONTACT_GAME_BLOCKING_FIELDS:
        if str(fields.get(key, "")).strip():
            return True
    # Explicit CRM/finance/security tagging also excludes the whole object.
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
    """Return only contacts already readable by actor and safe for the game.

    Payloads are minimized: no notes, addresses collection, history, sharing
    metadata, CRM fields or current field values are copied into the challenge
    engine.  Existing field names are passed only so the provider can prefer
    genuinely missing data-quality tasks.
    """
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
            data={
                "display_name": display_name[:200],
                "existing_fields": existing_fields,
            },
            # ContactStore.contacts(actor) has already applied owner/manager/
            # reader ACL filtering.  Do not construct candidates from contacts().
            normal_read_allowed=True,
            resource_class="contact",
            collection="contacts",
        ))
    return result


def _photo_is_blocked(document: dict[str, Any]) -> bool:
    values: list[str] = []
    for key in ("resource_class", "classification", "category", "document_type", "doctype"):
        value = document.get(key)
        if value not in (None, ""):
            values.append(str(value))
    tags = document.get("tags", [])
    if not isinstance(tags, list):
        return True
    values.extend(str(value) for value in tags if str(value).strip())
    if is_hard_excluded(*values):
        return True
    normalized = {str(value).strip().casefold() for value in values if str(value).strip()}
    return bool(normalized & PHOTO_GAME_BLOCKING_MARKERS)


def _safe_photo_document(root: str | Path, document: dict[str, Any]) -> Path | None:
    """Return the cached thumbnail for one explicitly uploaded safe photo.

    The original is deliberately never returned.  Missing or stale preview
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
    if Path(last_path).suffix.casefold() not in IMAGE_SUFFIXES or _photo_is_blocked(document):
        return None
    return PreviewService(root).cached_path(document, "thumbnail")


def image_candidates(root: str | Path, actor: str) -> list[Candidate]:
    """Expose only explicit photo uploads with an already generated safe preview.

    SimpleOffice's current document catalogue is authenticated application-wide,
    so this adapter does not invent narrower rights.  It only narrows that normal
    visibility further: explicit photo uploads, no sensitive markers, and a
    cached thumbnail are all mandatory.  No path, document id, EXIF, tag value or
    original URL is copied into the challenge payload.
    """
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
    """Apply a reviewed contact proposal without extending normal write rights.

    The first live game only fills missing fields. If another workflow populated
    the field after the challenge was shown, the proposal becomes stale and is
    refused rather than overwriting the newer value. ContactStore.patch_fields
    performs the authoritative manager/owner ACL check again while writing.
    """
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

    # patch_fields re-loads the contact under its normal write lock and performs
    # the authoritative owner/manager ACL check again before persisting.
    return store.patch_fields(contact_id, {field_name: normalized}, actor)
