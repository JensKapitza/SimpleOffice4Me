"""Archived contact lifecycle, sharing the store's permission checks and write lock."""

from __future__ import annotations

import uuid
from typing import Any

from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


class ContactConflict(ValueError):
    """A conditional contact write no longer matches the stored revision."""

    def __init__(self, contact: dict[str, Any] | None = None):
        super().__init__("contact was changed by another client")
        self.contact = contact


class ContactRecovery:
    """Recovery and removal operations of the file-backed contact store."""

    def _archive_contact_locked(self, contact: dict[str, Any], actor: str, reason: str, merged_into: str = "") -> None:
        archive = self._read(self.deleted_contacts_path, {"contacts": []})
        archive["contacts"] = [*archive.get("contacts", []), {
            "archive_id": str(uuid.uuid4()), "archived_at": utc_now(), "archived_by": actor,
            "reason": reason, "merged_into": merged_into, "contact": contact,
        }][-1000:]
        atomic_json_write(self.deleted_contacts_path, archive)

    def deleted_contacts(self, actor: str) -> list[dict[str, Any]]:
        principal = self._principal(actor)
        rows = [row for row in self._read(self.deleted_contacts_path, {"contacts": []}).get("contacts", [])
                if isinstance(row.get("contact"), dict) and self._can_manage(row["contact"], principal)]
        return sorted(rows, key=lambda row: row.get("archived_at", ""), reverse=True)

    def restore_deleted(self, archive_id: str, actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            archive = self._read(self.deleted_contacts_path, {"contacts": []})
            row = next((item for item in archive.get("contacts", []) if item.get("archive_id") == archive_id), None)
            if row is None or not isinstance(row.get("contact"), dict):
                raise ValueError("unknown deleted contact")
            contact = row["contact"]
            if not self._can_manage(contact, self._principal(actor)):
                raise ValueError("contact is not shared with this user")
            payload = self._read(self.contacts_path, {"contacts": []})
            restored = dict(contact)
            if any(item.get("contact_id") == restored.get("contact_id") for item in payload.get("contacts", [])):
                restored["contact_id"] = str(uuid.uuid4())
            restored["updated_at"], restored["updated_by"] = utc_now(), actor
            payload["contacts"].append(restored)
            archive["contacts"] = [item for item in archive.get("contacts", []) if item.get("archive_id") != archive_id]
            atomic_json_write(self.contacts_path, payload)
            atomic_json_write(self.deleted_contacts_path, archive)
            self.history.record("contact_restored", actor, "contacts", restored["contact_id"], restored)
            return restored

    def delete(self, contact_id: str, actor: str, expected_updated_at: str | None = None) -> None:
        self._require_actor(actor)
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            payload = self._read(self.contacts_path, {"contacts": []})
            contact = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None)
            if contact is None:
                raise ValueError("unknown contact")
            if not self._can_manage(contact, self._principal(actor)):
                raise ValueError("contact is not shared with this user")
            if expected_updated_at is not None and contact.get("updated_at", "") != expected_updated_at:
                raise ContactConflict(contact)
            self._archive_contact_locked(contact, actor, "deleted")
            payload["contacts"] = [item for item in payload["contacts"] if item.get("contact_id") != contact_id]
            atomic_json_write(self.contacts_path, payload)
            self.history.record("contact_deleted", actor, "contacts", contact_id, contact)
