"""File-based contacts with configurable field aliases for heterogeneous APIs."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory


DEFAULT_SCHEMA = {
    "required": ["display_name"],
    "aliases": {
        "first_name": ["first_name", "Vorname", "givenName"],
        "last_name": ["last_name", "Nachname", "familyName"],
        "display_name": ["display_name", "Name", "fn", "formattedName"],
        "email": ["email", "E-Mail", "mail"],
        "phone": ["phone", "Telefon", "mobile"],
        "birthday": ["birthday", "Geburtstag", "birthDate"],
        "company": ["company", "Firma", "organization"],
    },
}


class ContactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.contacts_path = self.control / "contacts.json"
        self.schema_path = self.control / "contact-schema.json"
        self.history = RevisionHistory(self.root)

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        if not self.schema_path.exists():
            atomic_json_write(self.schema_path, DEFAULT_SCHEMA)
        if not self.contacts_path.exists():
            atomic_json_write(self.contacts_path, {"contacts": []})

    def schema(self) -> dict[str, Any]:
        self.initialize()
        return self._read(self.schema_path, DEFAULT_SCHEMA)

    def save_schema(self, required: list[str], aliases: dict[str, list[str]], actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        schema = {"required": sorted(set(item.strip() for item in required if item.strip())), "aliases": {key.strip(): [str(value).strip() for value in values if str(value).strip()] for key, values in aliases.items() if key.strip()}}
        atomic_json_write(self.schema_path, schema)
        self.history.record("contact_schema_updated", actor, "contacts", "schema", schema)
        return schema

    def contacts(self) -> list[dict[str, Any]]:
        self.initialize()
        return sorted(self._read(self.contacts_path, {"contacts": []}).get("contacts", []), key=lambda item: item.get("fields", {}).get("display_name", "").casefold())

    def get(self, contact_id: str) -> dict[str, Any]:
        contact = next((item for item in self.contacts() if item.get("contact_id") == contact_id), None)
        if contact is None:
            raise ValueError("unknown contact")
        return contact

    def upsert(self, values: dict[str, str], actor: str, contact_id: str = "") -> dict[str, Any]:
        self._require_actor(actor)
        schema = self.schema()
        fields = self._normalize(values, schema)
        if not fields.get("display_name"):
            fields["display_name"] = " ".join(part for part in (fields.get("first_name", ""), fields.get("last_name", "")) if part).strip()
        missing = [field for field in schema.get("required", []) if not fields.get(field)]
        if missing:
            raise ValueError(f"required contact fields missing: {', '.join(missing)}")
        payload = self._read(self.contacts_path, {"contacts": []})
        existing = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None) if contact_id else None
        contact = {"contact_id": contact_id or str(uuid.uuid4()), "fields": fields, "created_at": existing.get("created_at", utc_now()) if existing else utc_now(), "updated_at": utc_now(), "updated_by": actor}
        payload["contacts"] = [item for item in payload["contacts"] if item.get("contact_id") != contact["contact_id"]] + [contact]
        atomic_json_write(self.contacts_path, payload)
        self.history.record("contact_updated" if existing else "contact_created", actor, "contacts", contact["contact_id"], contact)
        return contact

    def vcard(self, contact_id: str) -> str:
        fields = self.get(contact_id)["fields"]
        def value(key: str) -> str:
            return str(fields.get(key, "")).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        return "\r\n".join(["BEGIN:VCARD", "VERSION:4.0", f"FN:{value('display_name')}", f"N:{value('last_name')};{value('first_name')};;;", *([f"EMAIL:{value('email')}"] if fields.get("email") else []), *([f"TEL:{value('phone')}"] if fields.get("phone") else []), *([f"BDAY:{value('birthday')}"] if fields.get("birthday") else []), *([f"ORG:{value('company')}"] if fields.get("company") else []), "END:VCARD", ""])

    @staticmethod
    def _normalize(values: dict[str, str], schema: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for canonical, aliases in schema.get("aliases", {}).items():
            for key in [canonical, *aliases]:
                if str(values.get(key, "")).strip():
                    result[canonical] = str(values[key]).strip()
                    break
        result.update({key[7:]: str(value).strip() for key, value in values.items() if key.startswith("custom_") and str(value).strip()})
        return result

    @staticmethod
    def _read(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else default
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _require_actor(actor: str) -> None:
        if not actor.strip():
            raise ValueError("a named user is required for contact changes")
