"""File-based contacts with configurable field aliases for heterogeneous APIs."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
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
        self.carddav_path = self.control / "carddav.json"
        self.history = RevisionHistory(self.root)

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        if not self.schema_path.exists():
            atomic_json_write(self.schema_path, DEFAULT_SCHEMA)
        if not self.contacts_path.exists():
            atomic_json_write(self.contacts_path, {"contacts": []})
        if not self.carddav_path.exists():
            atomic_json_write(self.carddav_path, {"enabled": False, "accounts": []})

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
        contact = {"contact_id": contact_id or str(uuid.uuid4()), "fields": fields, "addresses": existing.get("addresses", []) if existing else [], "created_at": existing.get("created_at", utc_now()) if existing else utc_now(), "updated_at": utc_now(), "updated_by": actor}
        payload["contacts"] = [item for item in payload["contacts"] if item.get("contact_id") != contact["contact_id"]] + [contact]
        atomic_json_write(self.contacts_path, payload)
        self.history.record("contact_updated" if existing else "contact_created", actor, "contacts", contact["contact_id"], contact)
        return contact

    def add_address(self, contact_id: str, label: str, address: str, actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        if not address.strip():
            raise ValueError("address is required")
        payload = self._read(self.contacts_path, {"contacts": []})
        contact = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None)
        if contact is None:
            raise ValueError("unknown contact")
        normalized = " ".join(address.casefold().split())
        item = {"id": str(uuid.uuid4()), "label": label.strip() or "Adresse", "value": address.strip(), "normalized": normalized, "created_at": utc_now(), "created_by": actor}
        contact.setdefault("addresses", []).append(item)
        contact["updated_at"] = utc_now(); contact["updated_by"] = actor
        atomic_json_write(self.contacts_path, payload)
        self.history.record("contact_address_added", actor, "contacts", contact_id, contact)
        return item

    def address_matches(self) -> dict[str, list[str]]:
        matches: dict[str, list[str]] = {}
        for contact in self.contacts():
            for address in contact.get("addresses", []):
                matches.setdefault(address.get("normalized", ""), []).append(contact["contact_id"])
        return {key: value for key, value in matches.items() if key and len(value) > 1}

    def vcard(self, contact_id: str) -> str:
        contact = self.get(contact_id)
        fields = contact["fields"]
        def value(key: str) -> str:
            return str(fields.get(key, "")).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        return "\r\n".join(["BEGIN:VCARD", "VERSION:4.0", f"UID:{contact['contact_id']}", f"FN:{value('display_name')}", f"N:{value('last_name')};{value('first_name')};;;", *([f"EMAIL:{value('email')}"] if fields.get("email") else []), *([f"TEL:{value('phone')}"] if fields.get("phone") else []), *([f"BDAY:{value('birthday')}"] if fields.get("birthday") else []), *([f"ORG:{value('company')}"] if fields.get("company") else []), "END:VCARD", ""])

    def carddav(self) -> dict[str, Any]:
        self.initialize()
        config = self._read(self.carddav_path, {"enabled": False, "accounts": []})
        return {"enabled": config.get("enabled") is True, "accounts": [{key: value for key, value in item.items() if key not in ("password_hash", "password_salt")} for item in config.get("accounts", [])]}

    def activate_carddav(self, username: str, password: str, actor: str) -> None:
        self._require_actor(actor)
        if len(password) < 12:
            raise ValueError("CardDAV app password must contain at least 12 characters")
        self.initialize()
        salt = os.urandom(16)
        account = {"username": username, "enabled": True, "created_at": utc_now(), "password_salt": salt.hex(), "password_hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()}
        config = self._read(self.carddav_path, {"enabled": True, "accounts": []})
        config["enabled"] = True
        config["accounts"] = [item for item in config.get("accounts", []) if item.get("username") != username] + [account]
        atomic_json_write(self.carddav_path, config)
        self.history.record("carddav_activated", actor, "contacts", f"carddav-{username}", {"username": username, "enabled": True, "created_at": account["created_at"]})

    def carddav_authenticate(self, username: str, password: str) -> bool:
        self.initialize()
        config = self._read(self.carddav_path, {"enabled": False, "accounts": []})
        account = next((item for item in config.get("accounts", []) if item.get("username") == username and item.get("enabled") is True), None)
        if not config.get("enabled") or account is None:
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(account["password_salt"]), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(account["password_hash"]))

    def upsert_vcard(self, card: str, actor: str, contact_id: str = "") -> dict[str, Any]:
        values: dict[str, str] = {}
        for raw in card.replace("\r\n", "\n").split("\n"):
            key, separator, value = raw.partition(":")
            if not separator:
                continue
            name = key.split(";", 1)[0].upper()
            if name == "FN": values["display_name"] = value
            elif name == "N":
                parts = value.split(";")
                values["last_name"] = parts[0] if parts else ""
                values["first_name"] = parts[1] if len(parts) > 1 else ""
            elif name == "EMAIL": values["email"] = value
            elif name == "TEL": values["phone"] = value
            elif name == "BDAY": values["birthday"] = value
            elif name == "ORG": values["company"] = value
            elif name == "UID" and not contact_id: contact_id = value
        return self.upsert(values, actor, contact_id)

    def delete(self, contact_id: str, actor: str) -> None:
        self._require_actor(actor)
        payload = self._read(self.contacts_path, {"contacts": []})
        contact = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None)
        if contact is None:
            raise ValueError("unknown contact")
        payload["contacts"] = [item for item in payload["contacts"] if item.get("contact_id") != contact_id]
        atomic_json_write(self.contacts_path, payload)
        self.history.record("contact_deleted", actor, "contacts", contact_id, contact)

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
