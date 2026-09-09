"""Adapters from application stores into fail-closed gamification candidates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contact_store import ContactStore
from .gamification_engine import Candidate


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
