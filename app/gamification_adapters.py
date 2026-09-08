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
