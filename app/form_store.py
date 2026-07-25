"""Configurable business forms and records.

The form engine deliberately has no special table for invoices, products or
contacts.  Those entities are form definitions.  This keeps input masks,
exports and future business modules on one stable data model.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


FIELD_TYPES = {"text", "textarea", "email", "date", "number", "currency", "select", "relation"}

DEFAULT_FORMS = [
    {
        "form_id": "contact",
        "name": "Kontakt",
        "description": "Kunden, Lieferanten und Ansprechpartner mit frei erweiterbaren Stammdaten.",
        "title_field": "name",
        "fields": [
            {"key": "name", "label": "Name", "type": "text", "required": True},
            {"key": "role", "label": "Art", "type": "select", "options": ["Kunde", "Lieferant", "Ansprechpartner"], "required": True},
            {"key": "email", "label": "E-Mail", "type": "email"},
            {"key": "phone", "label": "Telefon", "type": "text"},
            {"key": "billing_address", "label": "Rechnungsadresse", "type": "textarea"},
            {"key": "delivery_address", "label": "Lieferadresse", "type": "textarea"},
            {"key": "payment_terms", "label": "Zahlungsbedingungen", "type": "text"},
            {"key": "status", "label": "Status", "type": "select", "options": ["aktiv", "inaktiv", "gesperrt"]},
            {"key": "notes", "label": "Notizen", "type": "textarea"},
        ],
    },
    {
        "form_id": "product",
        "name": "Produkt",
        "description": "Artikel, Leistungen und Varianten mit Preisen, Steuer und Lagerbezug.",
        "title_field": "name",
        "fields": [
            {"key": "article_number", "label": "Artikelnummer", "type": "text", "required": True},
            {"key": "name", "label": "Name", "type": "text", "required": True},
            {"key": "description", "label": "Beschreibung", "type": "textarea"},
            {"key": "purchase_price", "label": "Einkaufspreis", "type": "currency"},
            {"key": "sales_price", "label": "Verkaufspreis", "type": "currency"},
            {"key": "tax_rate", "label": "Steuersatz (%)", "type": "number"},
            {"key": "stock", "label": "Lagerbestand", "type": "number"},
            {"key": "supplier", "label": "Lieferant", "type": "relation", "relation_form": "contact"},
            {"key": "notes", "label": "Notizen", "type": "textarea"},
        ],
    },
    {
        "form_id": "invoice",
        "name": "Rechnung",
        "description": "Geschäftsdokument mit besonderen Feldern; die Prozessregeln bleiben am Formular konfigurierbar.",
        "title_field": "number",
        "fields": [
            {"key": "number", "label": "Rechnungsnummer", "type": "text", "required": True},
            {"key": "date", "label": "Rechnungsdatum", "type": "date", "required": True},
            {"key": "customer", "label": "Kunde", "type": "relation", "relation_form": "contact", "required": True},
            {"key": "status", "label": "Status", "type": "select", "options": ["Entwurf", "offen", "teilbezahlt", "bezahlt", "storniert"], "required": True},
            {"key": "payment_due", "label": "Fällig am", "type": "date"},
            {"key": "net_amount", "label": "Nettobetrag", "type": "currency"},
            {"key": "tax_amount", "label": "Steuerbetrag", "type": "currency"},
            {"key": "gross_amount", "label": "Bruttobetrag", "type": "currency"},
            {"key": "notes", "label": "Hinweis", "type": "textarea"},
        ],
    },
]


class FormStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.definitions_path = self.control / "form-definitions.json"
        self.records_path = self.control / "form-records.json"
        self.history = RevisionHistory(self.root)

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.control / ".forms-write.lock"):
            if not self.definitions_path.exists():
                atomic_json_write(self.definitions_path, {"forms": copy.deepcopy(DEFAULT_FORMS)})
            if not self.records_path.exists():
                atomic_json_write(self.records_path, {"records": []})

    def definitions(self) -> list[dict[str, Any]]:
        self.initialize()
        return self._read(self.definitions_path, {"forms": []}).get("forms", [])

    def definition(self, form_id: str) -> dict[str, Any]:
        form = next((item for item in self.definitions() if item.get("form_id") == form_id), None)
        if form is None:
            raise ValueError("unknown form")
        return form

    def save_definition(self, raw: dict[str, Any], actor: str) -> dict[str, Any]:
        self._require_actor(actor)
        form = self._normalize_definition(raw)
        with exclusive_file_lock(self.control / ".forms-write.lock"):
            payload = self._read(self.definitions_path, {"forms": []})
            existing = next((item for item in payload["forms"] if item.get("form_id") == form["form_id"]), None)
            payload["forms"] = [item for item in payload["forms"] if item.get("form_id") != form["form_id"]] + [form]
            atomic_json_write(self.definitions_path, payload)
            self.history.record("form_definition_updated" if existing else "form_definition_created", actor, "forms", form["form_id"], form)
        return form

    def records(self, form_id: str = "") -> list[dict[str, Any]]:
        self.initialize()
        records = self._read(self.records_path, {"records": []}).get("records", [])
        if form_id:
            records = [item for item in records if item.get("form_id") == form_id]
        return sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True)

    def record(self, form_id: str, record_id: str) -> dict[str, Any]:
        record = next((item for item in self.records(form_id) if item.get("record_id") == record_id), None)
        if record is None:
            raise ValueError("unknown form record")
        return record

    def save_record(self, form_id: str, values: dict[str, Any], actor: str, record_id: str = "") -> dict[str, Any]:
        self._require_actor(actor)
        form = self.definition(form_id)
        clean_values = self._normalize_values(form, values)
        with exclusive_file_lock(self.control / ".forms-write.lock"):
            payload = self._read(self.records_path, {"records": []})
            existing = next((item for item in payload["records"] if item.get("record_id") == record_id and item.get("form_id") == form_id), None) if record_id else None
            if record_id and existing is None:
                raise ValueError("unknown form record")
            now = utc_now()
            record = {
                "record_id": record_id or str(uuid.uuid4()), "form_id": form_id, "values": clean_values,
                "created_at": existing.get("created_at", now) if existing else now,
                "created_by": existing.get("created_by", actor) if existing else actor,
                "updated_at": now, "updated_by": actor,
            }
            payload["records"] = [item for item in payload["records"] if item.get("record_id") != record["record_id"]] + [record]
            atomic_json_write(self.records_path, payload)
            self.history.record("form_record_updated" if existing else "form_record_created", actor, "forms", record["record_id"], record)
        return record

    @staticmethod
    def _read(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else default
        except (OSError, json.JSONDecodeError):
            return default

    def _normalize_definition(self, raw: dict[str, Any]) -> dict[str, Any]:
        form_id = str(raw.get("form_id", "")).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", form_id):
            raise ValueError("form_id must use lowercase letters, digits, _ or -")
        fields = []
        for item in raw.get("fields", []):
            key = str(item.get("key", "")).strip().lower()
            field_type = str(item.get("type", "text")).strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError("field key is invalid")
            if field_type not in FIELD_TYPES:
                raise ValueError(f"unsupported field type: {field_type}")
            fields.append({"key": key, "label": str(item.get("label", key)).strip() or key, "type": field_type, "required": bool(item.get("required", False)), "options": [str(value).strip() for value in item.get("options", []) if str(value).strip()], "relation_form": str(item.get("relation_form", "")).strip()})
        if not fields or len({item["key"] for item in fields}) != len(fields):
            raise ValueError("a form needs unique fields")
        title_field = str(raw.get("title_field", fields[0]["key"])).strip()
        if title_field not in {item["key"] for item in fields}:
            raise ValueError("title_field must be a field key")
        return {"form_id": form_id, "name": str(raw.get("name", form_id)).strip() or form_id, "description": str(raw.get("description", "")).strip(), "title_field": title_field, "fields": fields}

    @staticmethod
    def _normalize_values(form: dict[str, Any], values: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for field in form.get("fields", []):
            key = field["key"]
            value = str(values.get(key, "")).strip()
            if field.get("required") and not value:
                raise ValueError(f"required field missing: {field['label']}")
            if field.get("type") == "select" and value and value not in field.get("options", []):
                raise ValueError(f"invalid selection: {field['label']}")
            result[key] = value
        return result

    @staticmethod
    def _require_actor(actor: str) -> None:
        if not actor.strip():
            raise ValueError("a named user is required for form changes")
