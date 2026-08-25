"""Import, export and comparison helpers for the contact workspace."""

from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path
from typing import Any

from .contact_management import ContactManagement
from .contact_store import ContactStore
from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


class ContactTools:
    IMPORT_LIMIT = 1000
    EXPORT_LIMIT = 1000

    def __init__(self, root: str | Path):
        self.store = ContactStore(root)
        self.store.initialize()
        self.management = ContactManagement(root)

    def export_selected(self, contact_ids: list[str], actor: str) -> str:
        ids = list(dict.fromkeys(contact_ids))[:self.EXPORT_LIMIT]
        if not ids:
            raise ValueError("no contacts selected")
        visible = {item["contact_id"]: item for item in self.store.contacts(actor)}
        if any(contact_id not in visible for contact_id in ids):
            raise ValueError("one or more contacts are not visible")
        return "".join(self.store.vcard(contact_id, actor) for contact_id in ids)

    def parse_csv(self, text: str) -> dict[str, Any]:
        if len(text.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("CSV import is limited to 2 MiB")
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV header row is missing")
        headers = [str(item or "").strip() for item in reader.fieldnames]
        rows: list[dict[str, str]] = []
        for number, raw in enumerate(reader, start=2):
            if len(rows) >= self.IMPORT_LIMIT:
                raise ValueError(f"CSV import is limited to {self.IMPORT_LIMIT} records")
            row = {str(key or "").strip(): str(value or "").strip() for key, value in raw.items() if str(key or "").strip()}
            if any(row.values()):
                row["__line__"] = str(number)
                rows.append(row)
        if not rows:
            raise ValueError("CSV contains no contact records")
        return {"headers": headers, "rows": rows, "delimiter": dialect.delimiter}

    def preview_csv(self, text: str, actor: str) -> dict[str, Any]:
        parsed = self.parse_csv(text)
        existing_emails = {
            str(item.get("fields", {}).get("email", "")).strip().casefold(): item
            for item in self.store.contacts(actor)
            if str(item.get("fields", {}).get("email", "")).strip()
        }
        preview: list[dict[str, Any]] = []
        valid = 0
        duplicates = 0
        invalid = 0
        for row in parsed["rows"]:
            values = {key: value for key, value in row.items() if key != "__line__"}
            try:
                fields = self.store._validated_fields(values)
                duplicate = bool(fields.get("email") and fields["email"].casefold() in existing_emails)
                valid += 1
                duplicates += int(duplicate)
                preview.append({"line": row["__line__"], "fields": fields, "duplicate": duplicate, "error": ""})
            except ValueError as exc:
                invalid += 1
                preview.append({"line": row["__line__"], "fields": values, "duplicate": False, "error": str(exc)})
        return {
            "headers": parsed["headers"],
            "delimiter": parsed["delimiter"],
            "rows": preview[:100],
            "total": len(parsed["rows"]),
            "valid": valid,
            "duplicates": duplicates,
            "invalid": invalid,
            "truncated": len(preview) > 100,
        }

    def import_csv(self, text: str, actor: str) -> dict[str, Any]:
        """Atomically create validated CSV contacts and skip exact e-mail duplicates.

        Existing records are never overwritten by CSV import.  This avoids
        accidental merges; the duplicate workspace remains the explicit place
        for deciding how two records should be combined.
        """
        self.store._require_actor(actor)
        parsed = self.parse_csv(text)
        validated: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        for row in parsed["rows"]:
            values = {key: value for key, value in row.items() if key != "__line__"}
            try:
                validated.append(self.store._validated_fields(values))
            except ValueError as exc:
                errors.append({"line": row["__line__"], "error": str(exc)})
        if errors:
            raise ValueError(f"CSV contains {len(errors)} invalid record(s); nothing was imported")

        principal = self.store._principal(actor)
        created = 0
        skipped = 0
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            email_index = {
                str(item.get("fields", {}).get("email", "")).strip().casefold()
                for item in payload.get("contacts", [])
                if str(item.get("fields", {}).get("email", "")).strip()
            }
            now = utc_now()
            new_contacts: list[dict[str, Any]] = []
            for fields in validated:
                email = str(fields.get("email", "")).strip().casefold()
                if email and email in email_index:
                    skipped += 1
                    continue
                contact_id = str(uuid.uuid4())
                contact = {
                    "contact_id": contact_id,
                    "fields": fields,
                    "addresses": [],
                    "owner": principal,
                    "managers": [],
                    "tags": [],
                    "groups": [],
                    "merged_from": [],
                    "changes": [
                        {"field": key, "old": "", "new": value, "at": now, "actor": actor}
                        for key, value in sorted(fields.items()) if value
                    ][-200:],
                    "created_at": now,
                    "created_by": actor,
                    "updated_at": now,
                    "updated_by": actor,
                    "source": {"provider": "csv_import"},
                }
                new_contacts.append(contact)
                if email:
                    email_index.add(email)
                created += 1
            payload["contacts"] = [*payload.get("contacts", []), *new_contacts]
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record("contacts_csv_imported", actor, "contacts", "csv-import", {"created": created, "skipped_duplicates": skipped, "contact_ids": [item["contact_id"] for item in new_contacts]})
        return {"created": created, "skipped_duplicates": skipped}

    def compare_snapshot(self, contact_id: str, snapshot_id: str, actor: str) -> dict[str, Any]:
        current = self.store.get(contact_id, actor)
        snapshot = next((row for row in self.management.snapshots(contact_id, actor) if row.get("snapshot_id") == snapshot_id), None)
        if snapshot is None:
            raise ValueError("unknown contact snapshot")
        old = snapshot["contact"]
        field_names = sorted(set(old.get("fields", {})) | set(current.get("fields", {})), key=str.casefold)
        fields = [
            {"field": key, "old": old.get("fields", {}).get(key, ""), "new": current.get("fields", {}).get(key, ""), "changed": old.get("fields", {}).get(key, "") != current.get("fields", {}).get(key, "")}
            for key in field_names
        ]
        metadata = [
            {"field": "tags", "old": old.get("tags", []), "new": current.get("tags", []), "changed": old.get("tags", []) != current.get("tags", [])},
            {"field": "groups", "old": old.get("groups", []), "new": current.get("groups", []), "changed": old.get("groups", []) != current.get("groups", [])},
            {"field": "managers", "old": old.get("managers", []), "new": current.get("managers", []), "changed": old.get("managers", []) != current.get("managers", [])},
            {"field": "addresses", "old": old.get("addresses", []), "new": current.get("addresses", []), "changed": old.get("addresses", []) != current.get("addresses", [])},
        ]
        return {"current": current, "snapshot": snapshot, "fields": fields, "metadata": metadata, "changed_count": sum(row["changed"] for row in [*fields, *metadata])}
