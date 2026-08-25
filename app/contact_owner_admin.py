"""Administrator-only repair helpers for legacy contacts without an owner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contact_store import ContactStore
from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


def ownerless_contacts(root: str | Path) -> list[dict[str, Any]]:
    """Return contacts that have no usable owner principal."""
    store = ContactStore(root)
    return [
        contact for contact in store.contacts()
        if not str(contact.get("owner") or "").strip()
    ]


def assign_ownerless_contacts(
    root: str | Path,
    contact_ids: list[str],
    new_owner: str,
    actor: str,
) -> int:
    """Assign only currently ownerless contacts and record each repair."""
    owner = str(new_owner or "").strip()
    performed_by = str(actor or "").strip()
    requested = {str(contact_id).strip() for contact_id in contact_ids if str(contact_id).strip()}
    if not owner:
        raise ValueError("new owner is required")
    if not performed_by:
        raise ValueError("actor is required")
    if not requested:
        raise ValueError("at least one contact is required")

    store = ContactStore(root)
    store.initialize()
    changed: list[dict[str, Any]] = []
    with exclusive_file_lock(store.control / ".contacts-write.lock"):
        payload = store._read(store.contacts_path, {"contacts": []})
        for contact in payload.get("contacts", []):
            contact_id = str(contact.get("contact_id") or "")
            if contact_id not in requested:
                continue
            if str(contact.get("owner") or "").strip():
                continue
            changed_at = utc_now()
            contact["owner"] = owner
            contact["updated_at"] = changed_at
            contact["updated_by"] = performed_by
            contact.setdefault("changes", []).append({
                "field": "owner",
                "old": "",
                "new": owner,
                "at": changed_at,
                "actor": performed_by,
            })
            contact["changes"] = contact["changes"][-200:]
            changed.append({"contact_id": contact_id, "owner": owner, "updated_at": changed_at})
        if changed:
            atomic_json_write(store.contacts_path, payload)

    for item in changed:
        store.history.record(
            "contact_owner_assigned",
            performed_by,
            "contacts",
            item["contact_id"],
            {"owner": owner, "updated_at": item["updated_at"], "reason": "admin_ownerless_repair"},
        )
    return len(changed)
