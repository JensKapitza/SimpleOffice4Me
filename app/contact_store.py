"""File-based contacts with configurable field aliases for heterogeneous APIs."""

from __future__ import annotations

import json
import base64
import binascii
import hashlib
import hmac
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory
from .file_lock import exclusive_file_lock


VCARD_EXPORT_CONFIG_KEY = "__vcard_export_fields__"
VCARD_EXPORT_FIELDS = (
    "display_name", "name", "nickname", "email", "phone", "birthday", "company",
    "department", "title", "role", "website", "note", "addresses", "categories",
    "groups", "relationships", "contact_type", "status", "customer_number",
    "supplier_number", "discount", "payment_terms", "payment_days", "currency",
    "vat_id", "tax_number", "bank_iban", "bank_bic", "unknown_properties",
)

VCARD_EXTENSION_FIELDS = {
    "X-SIMPLEOFFICE-RELATIONSHIPS": "relationships",
    "X-SIMPLEOFFICE-CONTACT-TYPE": "contact_type",
    "X-SIMPLEOFFICE-STATUS": "status",
    "X-SIMPLEOFFICE-CUSTOMER-NUMBER": "customer_number",
    "X-SIMPLEOFFICE-SUPPLIER-NUMBER": "supplier_number",
    "X-SIMPLEOFFICE-DISCOUNT": "discount",
    "X-SIMPLEOFFICE-PAYMENT-TERMS": "payment_terms",
    "X-SIMPLEOFFICE-PAYMENT-DAYS": "payment_days",
    "X-SIMPLEOFFICE-CURRENCY": "currency",
    "X-SIMPLEOFFICE-VAT-ID": "vat_id",
    "X-SIMPLEOFFICE-TAX-NUMBER": "tax_number",
    "X-SIMPLEOFFICE-BANK-IBAN": "bank_iban",
    "X-SIMPLEOFFICE-BANK-BIC": "bank_bic",
}

DEFAULT_SCHEMA = {
    "required": ["display_name"],
    "aliases": {
        "first_name": ["first_name", "Vorname", "givenName"],
        "last_name": ["last_name", "Nachname", "familyName"],
        "display_name": ["display_name", "Name", "fn", "formattedName"],
        "nickname": ["nickname", "Spitzname"],
        "email": ["email", "E-Mail", "mail"],
        "phone": ["phone", "Telefon", "mobile"],
        "birthday": ["birthday", "Geburtstag", "birthDate"],
        "company": ["company", "Firma", "organization"],
        "department": ["department", "Abteilung", "organizationalUnit"],
        "title": ["title", "Position", "jobTitle"],
        "role": ["role", "Rolle"],
        "website": ["website", "Webseite", "url"],
        "note": ["note", "Notiz", "notes"],
    },
}


class ContactConflict(ValueError):
    """A conditional contact write no longer matches the stored revision."""

    def __init__(self, contact: dict[str, Any] | None = None):
        super().__init__("contact was changed by another client")
        self.contact = contact


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
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            if not self.schema_path.exists():
                atomic_json_write(self.schema_path, DEFAULT_SCHEMA)
            if not self.contacts_path.exists():
                atomic_json_write(self.contacts_path, {"contacts": []})
            if not self.carddav_path.exists():
                atomic_json_write(self.carddav_path, {"enabled": False, "accounts": []})

    @staticmethod
    def _vcard_export_fields_from_aliases(aliases: dict[str, Any]) -> list[str]:
        configured = aliases.get(VCARD_EXPORT_CONFIG_KEY)
        if not isinstance(configured, list):
            return list(VCARD_EXPORT_FIELDS)
        selected = {str(value).strip() for value in configured}
        return [field for field in VCARD_EXPORT_FIELDS if field in selected]

    def schema(self) -> dict[str, Any]:
        self.initialize()
        stored = self._read(self.schema_path, DEFAULT_SCHEMA)
        aliases = dict(DEFAULT_SCHEMA["aliases"])
        aliases.update(stored.get("aliases", {}))
        return {
            "required": stored.get("required", DEFAULT_SCHEMA["required"]),
            "aliases": aliases,
            "vcard_export_fields": self._vcard_export_fields_from_aliases(aliases),
        }

    def vcard_export_fields(self) -> set[str]:
        return set(self.schema().get("vcard_export_fields", VCARD_EXPORT_FIELDS))

    def save_schema(self, required: list[str], aliases: dict[str, list[str]], actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        cleaned_aliases: dict[str, list[str]] = {}
        for key, values in aliases.items():
            key = key.strip()
            if not key or not isinstance(values, list):
                continue
            if key == VCARD_EXPORT_CONFIG_KEY:
                selected = {str(value).strip() for value in values}
                cleaned_aliases[key] = [field for field in VCARD_EXPORT_FIELDS if field in selected]
            else:
                cleaned_aliases[key] = [str(value).strip() for value in values if str(value).strip()]
        schema = {
            "required": sorted(set(item.strip() for item in required if item.strip())),
            "aliases": cleaned_aliases,
        }
        atomic_json_write(self.schema_path, schema)
        self.history.record(
            "contact_schema_updated", actor, "contacts", "schema",
            {**schema, "vcard_export_fields": self._vcard_export_fields_from_aliases(cleaned_aliases)},
        )
        return self.schema()

    def contacts(self, actor: str = "") -> list[dict[str, Any]]:
        self.initialize()
        items = self._read(self.contacts_path, {"contacts": []}).get("contacts", [])
        if actor:
            principal = self._principal(actor)
            items = [item for item in items if self._can_read(item, principal)]
        return sorted(items, key=lambda item: item.get("fields", {}).get("display_name", "").casefold())

    def search(
        self,
        query: str,
        actor: str = "",
        *,
        contacts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Find visible contacts across standard, custom, metadata and address fields."""
        items = contacts if contacts is not None else self.contacts(actor)
        needle = query.strip().casefold()
        if not needle:
            return items
        return [
            contact for contact in items
            if needle in " ".join([
                contact.get("contact_id", ""),
                *[str(value) for value in contact.get("fields", {}).values()],
                *[str(value) for value in contact.get("tags", [])],
                *[str(value) for value in contact.get("groups", [])],
                *[str(address.get("label", "")) + " " + str(address.get("value", "")) for address in contact.get("addresses", [])],
            ]).casefold()
        ]

    def get(self, contact_id: str, actor: str = "") -> dict[str, Any]:
        contact = next((item for item in self.contacts() if item.get("contact_id") == contact_id), None)
        if contact is None:
            raise ValueError("unknown contact")
        if actor and not self._can_read(contact, self._principal(actor)):
            raise ValueError("contact is not shared with this user")
        return contact

    def can_manage(self, contact_id: str, actor: str) -> bool:
        contact = next((item for item in self.contacts() if item.get("contact_id") == contact_id), None)
        return bool(contact and self._can_manage(contact, self._principal(actor)))

    def can_manage_contact(self, contact: dict[str, Any], actor: str) -> bool:
        """Check a contact that was already loaded without rereading the store."""
        return self._can_manage(contact, self._principal(actor))

    def upsert(self, values: dict[str, str], actor: str, contact_id: str = "", source: dict[str, str] | None = None) -> dict[str, Any]:
        self._require_actor(actor)
        fields = self._validated_fields(values)
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            return self._upsert_locked(fields, actor, contact_id, source)

    def patch_fields(self, contact_id: str, changes: dict[str, str], actor: str) -> dict[str, Any]:
        """Atomically change selected fields while retaining every other field."""
        self._require_actor(actor)
        schema = self.schema()
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            payload = self._read(self.contacts_path, {"contacts": []})
            existing = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None)
            if existing is None:
                raise ValueError("unknown contact")
            if not self._can_manage(existing, self._principal(actor)):
                raise ValueError("contact is not shared with this user")
            merged = dict(existing.get("fields", {}))
            for key, value in changes.items():
                key = str(key).strip()
                if not key or key.startswith("__"):
                    continue
                normalized = str(value).strip()
                if normalized:
                    merged[key] = normalized
                else:
                    merged.pop(key, None)
            values = {
                (key if key in schema.get("aliases", {}) else f"custom_{key}"): value
                for key, value in merged.items()
            }
            fields = self._normalize(values, schema)
            if not fields.get("display_name"):
                fields["display_name"] = " ".join(
                    part for part in (fields.get("first_name", ""), fields.get("last_name", "")) if part
                ).strip()
            missing = [field for field in schema.get("required", []) if not fields.get(field)]
            if missing:
                raise ValueError(f"required contact fields missing: {', '.join(missing)}")
            return self._upsert_locked(fields, actor, contact_id, payload=payload)

    def _validated_fields(self, values: dict[str, str]) -> dict[str, str]:
        schema = self.schema()
        fields = self._normalize(values, schema)
        if not fields.get("display_name"):
            fields["display_name"] = " ".join(part for part in (fields.get("first_name", ""), fields.get("last_name", "")) if part).strip()
        missing = [field for field in schema.get("required", []) if not fields.get(field)]
        if missing:
            raise ValueError(f"required contact fields missing: {', '.join(missing)}")
        return fields

    def _upsert_locked(self, fields: dict[str, str], actor: str, contact_id: str = "", source: dict[str, str] | None = None, payload: dict[str, Any] | None = None, metadata: dict[str, list[str]] | None = None) -> dict[str, Any]:
        payload = payload or self._read(self.contacts_path, {"contacts": []})
        existing = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None) if contact_id else None
        if existing is None and source and source.get("source_id"):
            existing = next((item for item in payload["contacts"] if item.get("source", {}).get("source_id") == source["source_id"] and item.get("source", {}).get("provider") == source.get("provider")), None)
        if existing and not contact_id:
            contact_id = existing["contact_id"]
        principal = self._principal(actor)
        if existing and not self._can_manage(existing, principal):
            raise ValueError("contact is not shared with this user")
        changed_at = utc_now()
        changes = list(existing.get("changes", [])) if existing else []
        old_fields = existing.get("fields", {}) if existing else {}
        for field in sorted(set(old_fields) | set(fields)):
            if old_fields.get(field, "") != fields.get(field, ""):
                changes.append({"field": field, "old": old_fields.get(field, ""), "new": fields.get(field, ""), "at": changed_at, "actor": actor})
        tags = list(existing.get("tags", [])) if existing else []
        groups = list(existing.get("groups", [])) if existing else []
        if metadata is not None:
            if "tags" in metadata:
                tags = self._clean_metadata_values(metadata.get("tags", []))
            if "groups" in metadata:
                groups = self._clean_metadata_values(metadata.get("groups", []))
        contact = {
            "contact_id": contact_id or str(uuid.uuid4()),
            "fields": fields,
            "addresses": existing.get("addresses", []) if existing else [],
            "owner": existing.get("owner") or principal if existing else principal,
            "managers": existing.get("managers", []) if existing else [],
            "readers": existing.get("readers", []) if existing else [],
            "tags": tags,
            "groups": groups,
            "merged_from": existing.get("merged_from", []) if existing else [],
            "changes": changes[-200:],
            "created_at": existing.get("created_at", changed_at) if existing else changed_at,
            "created_by": existing.get("created_by", actor) if existing else actor,
            "updated_at": changed_at,
            "updated_by": actor,
            "source": source or existing.get("source", {}) if existing else (source or {}),
        }
        payload["contacts"] = [item for item in payload["contacts"] if item.get("contact_id") != contact["contact_id"]] + [contact]
        atomic_json_write(self.contacts_path, payload)
        self.history.record("contact_updated" if existing else "contact_created", actor, "contacts", contact["contact_id"], contact)
        return contact

    @staticmethod
    def _clean_metadata_values(values: list[str]) -> list[str]:
        return sorted({" ".join(str(value).strip().split()) for value in values if str(value).strip()}, key=str.casefold)[:100]

    @staticmethod
    def format_postal_address(components: dict[str, str]) -> str:
        """Format a structured address without discarding country-specific parts."""
        clean = {key: " ".join(str(value).strip().split()) for key, value in components.items()}
        street, city = clean.get("street", ""), clean.get("city", "")
        state, postal = clean.get("state", ""), clean.get("postal", "")
        country = clean.get("country", "").upper()
        if country in {"US", "CA", "AU"}:
            locality = ", ".join(part for part in (city, state) if part)
            locality = " ".join(part for part in (locality, postal) if part)
            rows = (street, locality, country)
        elif country == "JP":
            rows = (postal, " ".join(part for part in (state, city) if part), street, country)
        elif country in {"GB", "IE"}:
            rows = (street, city, postal, country)
        else:
            rows = (street, " ".join(part for part in (postal, city) if part), state, country)
        return "\n".join(row for row in rows if row)

    def add_address(self, contact_id: str, label: str, address: str, actor: str, components: dict[str, str] | None = None) -> dict[str, Any]:
        self._require_actor(actor)
        components = {key: str(value).strip() for key, value in (components or {}).items() if str(value).strip()}
        address = address.strip() or self.format_postal_address(components)
        if not address.strip():
            raise ValueError("address is required")
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            payload = self._read(self.contacts_path, {"contacts": []})
            contact = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None)
            if contact is None:
                raise ValueError("unknown contact")
            if not self._can_manage(contact, self._principal(actor)):
                raise ValueError("contact is not shared with this user")
            normalized = " ".join(address.casefold().split())
            item = {"id": str(uuid.uuid4()), "label": label.strip() or "Adresse", "value": address.strip(), "normalized": normalized, "components": components, "created_at": utc_now(), "created_by": actor}
            contact.setdefault("addresses", []).append(item)
            contact["updated_at"] = utc_now(); contact["updated_by"] = actor
            atomic_json_write(self.contacts_path, payload)
            self.history.record("contact_address_added", actor, "contacts", contact_id, contact)
        return item

    def share(self, contact_id: str, managers: list[str], actor: str, readers: list[str] | None = None) -> dict[str, Any]:
        self._require_actor(actor)
        principal = self._principal(actor)
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            payload = self._read(self.contacts_path, {"contacts": []})
            contact = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None)
            if contact is None:
                raise ValueError("unknown contact")
            owner = contact.get("owner") or self._principal(str(contact.get("created_by", ""))) or principal
            if owner != principal:
                raise ValueError("only the contact owner may change sharing")
            manager_set = {item.strip() for item in managers if item.strip() and item.strip() != owner}
            if readers is None:
                reader_set = set(contact.get("readers", []))
            else:
                reader_set = {item.strip() for item in readers if item.strip() and item.strip() != owner}
            reader_set -= manager_set
            contact["owner"] = owner
            contact["managers"] = sorted(manager_set, key=str.casefold)
            contact["readers"] = sorted(reader_set, key=str.casefold)
            contact["updated_at"] = utc_now()
            contact["updated_by"] = actor
            atomic_json_write(self.contacts_path, payload)
            self.history.record("contact_sharing_updated", actor, "contacts", contact_id, {"owner": owner, "managers": contact["managers"], "readers": contact["readers"], "updated_at": contact["updated_at"]})
        return contact

    def address_matches(
        self, contacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, list[str]]:
        matches: dict[str, list[str]] = {}
        for contact in contacts if contacts is not None else self.contacts():
            for address in contact.get("addresses", []):
                matches.setdefault(address.get("normalized", ""), []).append(contact["contact_id"])
        return {key: value for key, value in matches.items() if key and len(value) > 1}

    @staticmethod
    def _safe_raw_vcard_line(line: str) -> str:
        raw = str(line)
        if "\r" in raw or "\n" in raw:
            return ""
        line = raw.strip()
        if "BEGIN:VCARD" in line.upper() or "END:VCARD" in line.upper():
            return ""
        key, sep, _ = line.partition(":")
        name = key.split(";", 1)[0].rsplit(".", 1)[-1].upper()
        if not sep or not re.fullmatch(r"[A-Z0-9-]{1,80}", name):
            return ""
        if name in {"BEGIN", "END", "VERSION", "UID", "FN", "N", "BDAY", "ORG", "NICKNAME", "TITLE", "ROLE", "URL", "NOTE", "CATEGORIES", "X-SIMPLEOFFICE-GROUP"}:
            return ""
        # Binary PHOTO values are frequently folded into one very long base64
        # line.  Truncating them made otherwise valid contact pictures corrupt.
        return line[:10 * 1024 * 1024] if name == "PHOTO" else line[:4000]

    def photo(self, contact_id: str, actor: str = "") -> tuple[bytes, str]:
        """Decode a safe raster PHOTO property retained from a vCard."""
        fields = self.get(contact_id, actor).get("fields", {})
        raw = next((str(value) for key, value in fields.items() if key.startswith("vcard_") and self._vcard_property_name(str(value)) == "PHOTO"), "")
        header, separator, encoded = raw.partition(":")
        if not separator:
            raise ValueError("contact has no embedded photo")
        params = header.upper().split(";")[1:]
        if not any(item in {"ENCODING=B", "ENCODING=BASE64"} for item in params) and not encoded.casefold().startswith("data:image/"):
            raise ValueError("contact photo is not embedded base64")
        declared = next((item.split("=", 1)[1] for item in params if item.startswith("TYPE=") or item.startswith("MEDIATYPE=")), "")
        if encoded.casefold().startswith("data:image/"):
            media_header, _, encoded = encoded.partition(",")
            declared = media_header[5:].split(";", 1)[0]
        try:
            payload = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("contact photo contains invalid base64") from exc
        if not payload or len(payload) > 8 * 1024 * 1024:
            raise ValueError("contact photo size is invalid")
        signatures = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))
        media_type = next((mime for magic, mime in signatures if payload.startswith(magic)), "")
        if not media_type and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
            media_type = "image/webp"
        if not media_type:
            raise ValueError(f"unsupported embedded contact photo type: {declared or 'unknown'}")
        return payload, media_type

    def has_photo(self, contact: dict[str, Any]) -> bool:
        return any(key.startswith("vcard_") and self._vcard_property_name(str(value)) == "PHOTO" for key, value in contact.get("fields", {}).items())

    @staticmethod
    def _vcard_property_name(line: str) -> str:
        return line.partition(":")[0].split(";", 1)[0].rsplit(".", 1)[-1].upper()

    @staticmethod
    def _vcard_property_signature(line: str) -> str:
        """Return a stable property/parameter signature without its value."""
        header = line.partition(":")[0]
        property_part, *parameters = header.split(";")
        normalized_parameters = sorted(parameter.strip().upper() for parameter in parameters if parameter.strip())
        return ";".join((property_part.strip().upper(), *normalized_parameters))

    def vcard(self, contact_id: str, actor: str = "") -> str:
        contact = self.get(contact_id, actor)
        fields = contact["fields"]
        released = self.vcard_export_fields()

        def value(key: str) -> str:
            return str(fields.get(key, "")).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

        def text(raw_value: str) -> str:
            return str(raw_value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

        categories = ",".join(text(item) for item in contact.get("tags", []))
        groups = ",".join(text(item) for item in contact.get("groups", []))
        extras = []
        for key in sorted(fields):
            if key.startswith("vcard_"):
                raw = self._safe_raw_vcard_line(fields[key])
                if raw:
                    extras.append(raw)
        org = value("company")
        if fields.get("department"):
            org += ";" + value("department")

        raw_lines = [
            "BEGIN:VCARD", "VERSION:4.0", f"UID:{contact['contact_id']}", f"FN:{value('display_name')}",
            f"N:{value('last_name')};{value('first_name')};;;",
            *([f"NICKNAME:{value('nickname')}"] if fields.get("nickname") else []),
            *([f"EMAIL:{value('email')}"] if fields.get("email") else []),
            *([f"TEL:{value('phone')}"] if fields.get("phone") else []),
            *([f"BDAY:{value('birthday')}"] if fields.get("birthday") else []),
            *([f"ORG:{org}"] if org else []),
            *([f"TITLE:{value('title')}"] if fields.get("title") else []),
            *([f"ROLE:{value('role')}"] if fields.get("role") else []),
            *([f"URL:{value('website')}"] if fields.get("website") else []),
            *([f"NOTE:{value('note')}"] if fields.get("note") else []),
            *([f"CATEGORIES:{categories}"] if categories else []),
            *([f"X-SIMPLEOFFICE-GROUP:{groups}"] if groups else []),
            *extras,
        ]

        property_to_field = {
            "FN": "display_name", "N": "name", "NICKNAME": "nickname", "EMAIL": "email",
            "TEL": "phone", "BDAY": "birthday", "ORG": "company", "TITLE": "title",
            "ROLE": "role", "URL": "website", "NOTE": "note", "CATEGORIES": "categories",
            "X-SIMPLEOFFICE-GROUP": "groups",
        }
        lines = ["BEGIN:VCARD", "VERSION:4.0", f"UID:{contact['contact_id']}"]
        for line in raw_lines[3:]:
            name = self._vcard_property_name(line)
            field = property_to_field.get(name)
            if name == "FN" and "display_name" not in released:
                lines.append("FN:")
            elif field is not None:
                if field in released:
                    lines.append(line)
            elif "unknown_properties" in released:
                lines.append(line)

        if "department" not in released and "company" in released:
            for index, line in enumerate(lines):
                if self._vcard_property_name(line) == "ORG":
                    lines[index] = f"ORG:{value('company')}"

        if "addresses" in released:
            for address in contact.get("addresses", []):
                address_value = address.get("value", "")
                components = address.get("components", {})
                if not address_value and not components:
                    continue
                label = str(address.get("label", "")).strip().casefold()
                address_type = "work" if label in {"firma", "arbeit", "work", "office"} else "home" if label in {"privat", "home"} else "other"
                if components:
                    parts = ("", "", components.get("street", ""), components.get("city", ""), components.get("state", ""), components.get("postal", ""), components.get("country", ""))
                    lines.append(f"ADR;TYPE={address_type}:" + ";".join(text(part) for part in parts))
                else:
                    lines.append(f"ADR;TYPE={address_type}:;;{text(address_value)};;;;")

        for property_name, field in VCARD_EXTENSION_FIELDS.items():
            if field in released and fields.get(field):
                lines.append(f"{property_name}:{value(field)}")

        lines.extend(["END:VCARD", ""])
        return "\r\n".join(lines)

    def export_vcards(self, actor: str = "") -> str:
        """Export all contacts as one portable vCard 4.0 file."""
        return "".join(self.vcard(contact["contact_id"], actor) for contact in self.contacts(actor))

    def import_vcards(self, content: str, actor: str) -> int:
        """Import vCards; an existing UID updates the existing contact."""
        self._require_actor(actor)
        cards: list[str] = []
        current: list[str] = []
        for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line.upper() == "BEGIN:VCARD":
                current = [line]
            elif current:
                current.append(line)
                if line.upper() == "END:VCARD":
                    cards.append("\r\n".join(current) + "\r\n")
                    current = []
        if not cards:
            raise ValueError("no vCard records found")
        for card in cards:
            self.upsert_vcard(card, actor)
        self.history.record("contacts_imported", actor, "contacts", "vcard-import", {"count": len(cards)})
        return len(cards)

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
        values, contact_id, metadata = self._vcard_values(card, contact_id)
        fields = self._validated_fields(values)
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            return self._upsert_locked(fields, actor, contact_id, metadata=metadata)

    def conditional_upsert_vcard(self, card: str, actor: str, contact_id: str, expected_updated_at: str | None = None, create_only: bool = False) -> dict[str, Any]:
        """Apply a DAV precondition and write atomically under the same lock."""
        self._require_actor(actor)
        values, contact_id, metadata = self._vcard_values(card, contact_id)
        fields = self._validated_fields(values)
        released = self.vcard_export_fields()
        with exclusive_file_lock(self.control / ".contacts-write.lock"):
            payload = self._read(self.contacts_path, {"contacts": []})
            existing = next((item for item in payload["contacts"] if item.get("contact_id") == contact_id), None)
            if existing and not self._can_manage(existing, self._principal(actor)):
                raise ValueError("contact is not shared with this user")
            if create_only and existing is not None:
                raise ContactConflict(existing)
            if expected_updated_at is not None and (existing is None or existing.get("updated_at", "") != expected_updated_at):
                raise ContactConflict(existing)
            if existing is not None:
                fields = self._preserve_carddav_fields(fields, existing.get("fields", {}), released)
            return self._upsert_locked(fields, actor, contact_id, payload=payload, metadata=metadata)

    def _preserve_carddav_fields(self, incoming: dict[str, str], existing: dict[str, str], released: set[str]) -> dict[str, str]:
        """Keep fields a CardDAV client could not see or cannot represent."""
        merged = dict(incoming)
        field_policy = {
            "first_name": "name", "last_name": "name", "department": "department",
        }
        incoming_by_signature: dict[str, list[str]] = {}
        for key, value in incoming.items():
            if key.startswith("vcard_") and self._safe_raw_vcard_line(value):
                incoming_by_signature.setdefault(self._vcard_property_signature(value), []).append(value)
        remaining_incoming = {signature: list(values) for signature, values in incoming_by_signature.items()}
        preserved_index = 0
        for key, value in existing.items():
            if key in merged:
                if not key.startswith("vcard_"):
                    continue
                if merged[key] == value:
                    signature = self._vcard_property_signature(value)
                    candidates = remaining_incoming.get(signature, [])
                    if value in candidates:
                        candidates.remove(value)
                    continue
            if key.startswith("vcard_"):
                safe = self._safe_raw_vcard_line(value)
                if not safe:
                    continue
                signature = self._vcard_property_signature(safe)
                candidates = remaining_incoming.get(signature, [])
                if safe in candidates:
                    candidates.remove(safe)
                    continue
                if candidates:
                    candidates.pop(0)
                    continue
                target = key
                while target in merged:
                    preserved_index += 1
                    target = f"vcard_preserved_{preserved_index:03d}_{self._vcard_property_name(safe).casefold()}"
                merged[target] = safe
                continue
            policy_key = field_policy.get(key, key)
            if policy_key not in released:
                merged[key] = value
        return merged

    @staticmethod
    def _vcard_values(card: str, contact_id: str = "") -> tuple[dict[str, str], str, dict[str, list[str]]]:
        values: dict[str, str] = {}
        metadata: dict[str, list[str]] = {}
        lines: list[str] = []
        for physical_line in card.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if physical_line.startswith((" ", "\t")) and lines:
                lines[-1] += physical_line[1:]
            else:
                lines.append(physical_line)
        seen_email = False
        seen_phone = False
        extra_index = 0
        for raw in lines:
            key, separator, raw_value = raw.partition(":")
            if not separator:
                continue
            name = key.split(";", 1)[0].rsplit(".", 1)[-1].upper()
            value = ContactStore._unescape_vcard_text(raw_value)
            if name == "FN": values["display_name"] = value
            elif name == "N":
                parts = ContactStore._split_vcard_components(raw_value)
                values["last_name"] = parts[0] if parts else ""
                values["first_name"] = parts[1] if len(parts) > 1 else ""
            elif name == "NICKNAME": values["nickname"] = value
            elif name == "EMAIL" and not seen_email:
                values["email"] = value; seen_email = True
            elif name == "TEL" and not seen_phone:
                values["phone"] = value; seen_phone = True
            elif name == "BDAY": values["birthday"] = value
            elif name == "ORG":
                parts = ContactStore._split_vcard_components(raw_value)
                values["company"] = parts[0] if parts else ""
                values["department"] = parts[1] if len(parts) > 1 else ""
            elif name == "TITLE": values["title"] = value
            elif name == "ROLE": values["role"] = value
            elif name == "URL": values["website"] = value
            elif name == "NOTE": values["note"] = value
            elif name == "CATEGORIES": metadata["tags"] = ContactStore._split_vcard_list(raw_value)
            elif name == "X-SIMPLEOFFICE-GROUP": metadata["groups"] = ContactStore._split_vcard_list(raw_value)
            elif name in VCARD_EXTENSION_FIELDS: values[f"custom_{VCARD_EXTENSION_FIELDS[name]}"] = value
            elif name == "UID" and not contact_id: contact_id = value
            elif name not in {"BEGIN", "END", "VERSION"}:
                safe = ContactStore._safe_raw_vcard_line(raw)
                if safe:
                    values[f"custom_vcard_{extra_index:03d}_{name.casefold()}"] = safe
                    extra_index += 1
        return values, contact_id, metadata

    @staticmethod
    def _split_vcard_list(value: str) -> list[str]:
        items: list[str] = []
        current: list[str] = []
        escaped = False
        for character in value:
            if escaped:
                current.extend(("\\", character)); escaped = False
            elif character == "\\":
                escaped = True
            elif character == ",":
                item = ContactStore._unescape_vcard_text("".join(current)).strip()
                if item: items.append(item)
                current = []
            else:
                current.append(character)
        if escaped: current.append("\\")
        item = ContactStore._unescape_vcard_text("".join(current)).strip()
        if item: items.append(item)
        return ContactStore._clean_metadata_values(items)

    @staticmethod
    def _split_vcard_components(value: str) -> list[str]:
        parts: list[str] = []
        current: list[str] = []
        escaped = False
        for character in value:
            if escaped:
                current.extend(("\\", character))
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == ";":
                parts.append(ContactStore._unescape_vcard_text("".join(current)))
                current = []
            else:
                current.append(character)
        if escaped:
            current.append("\\")
        parts.append(ContactStore._unescape_vcard_text("".join(current)))
        return parts

    @staticmethod
    def _unescape_vcard_text(value: str) -> str:
        result: list[str] = []
        index = 0
        while index < len(value):
            if value[index] != "\\" or index + 1 >= len(value):
                result.append(value[index])
                index += 1
                continue
            escaped = value[index + 1]
            if escaped in ("n", "N"):
                result.append("\n")
            elif escaped in ("\\", ",", ";"):
                result.append(escaped)
            else:
                result.extend(("\\", escaped))
            index += 2
        return "".join(result)

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
            payload["contacts"] = [item for item in payload["contacts"] if item.get("contact_id") != contact_id]
            atomic_json_write(self.contacts_path, payload)
            self.history.record("contact_deleted", actor, "contacts", contact_id, contact)

    @staticmethod
    def _normalize(values: dict[str, str], schema: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for canonical, aliases in schema.get("aliases", {}).items():
            if canonical.startswith("__"):
                continue
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

    @staticmethod
    def _principal(actor: str) -> str:
        return actor.split(":", 1)[1] if actor.startswith("carddav:") else actor

    @staticmethod
    def _can_manage(contact: dict[str, Any], principal: str) -> bool:
        owner = str(contact.get("owner", "")).strip() or ContactStore._principal(str(contact.get("created_by", "")))
        return bool(owner) and (principal == owner or principal in contact.get("managers", []))

    @staticmethod
    def _can_read(contact: dict[str, Any], principal: str) -> bool:
        return ContactStore._can_manage(contact, principal) or principal in contact.get("readers", [])
