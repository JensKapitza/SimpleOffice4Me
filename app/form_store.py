"""Configurable business forms and records.

Contacts are master data and are therefore managed exclusively by
``ContactStore``.  Business forms can reference them.  Invoice headers stay
configurable, while invoice positions and their calculated totals are kept on
the invoice record so that a price calculation cannot be overwritten by a
manually entered total.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


FIELD_TYPES = {"text", "textarea", "email", "date", "number", "currency", "select", "relation"}


class StockAdjustmentCommittedError(RuntimeError):
    """The embedded stock movement is committed, but secondary history failed."""

DEFAULT_FORMS = [
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
        "description": "Rechnungskopf mit Kontakt, Positionen und automatisch berechneten Summen.",
        "layout": "invoice",
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
            else:
                payload = self._read(self.definitions_path, {"forms": []})
                upgraded = self._upgrade_standard_definitions(payload.get("forms", []))
                if upgraded != payload.get("forms", []):
                    atomic_json_write(self.definitions_path, {"forms": upgraded})
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
        if form["form_id"] == "contact":
            raise ValueError("contacts are master data and are managed under Kontakte")
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

    def save_record(self, form_id: str, values: dict[str, Any], actor: str, record_id: str = "", expected_updated_at: str = "") -> dict[str, Any]:
        self._require_actor(actor)
        form = self.definition(form_id)
        tracks_stock = form_id == "product" and any(
            field.get("key") == "stock" and field.get("type") == "number"
            for field in form.get("fields", [])
        )
        line_items = self._normalize_line_items(values.get("line_items", [])) if form.get("layout") == "invoice" else None
        with exclusive_file_lock(self.control / ".forms-write.lock"):
            payload = self._read(self.records_path, {"records": []})
            existing = next((item for item in payload["records"] if item.get("record_id") == record_id and item.get("form_id") == form_id), None) if record_id else None
            if record_id and existing is None:
                raise ValueError("unknown form record")
            if existing and expected_updated_at and expected_updated_at != existing.get("updated_at", ""):
                raise ValueError("record changed since it was opened; reload and try again")
            if existing and form_id == "product":
                previous_values = existing.get("values", {})
                previous_stock = str(previous_values.get("stock", ""))
                submitted_stock = values.get("stock")
                if submitted_stock is not None and str(submitted_stock).strip() != previous_stock:
                    raise ValueError("stock changes must use a stock adjustment")
                if tracks_stock:
                    values = {**values, "stock": previous_stock}
            clean_values = self._normalize_values(form, values)
            if existing and form_id == "product" and "stock" in existing.get("values", {}):
                clean_values["stock"] = str(existing["values"].get("stock", ""))
            if line_items is not None:
                clean_values.update(self._invoice_totals(line_items))
            if tracks_stock and not existing:
                initial_stock = clean_values.get("stock", "")
                if initial_stock:
                    initial_amount = self._amount(initial_stock, "initial stock")
                    if not initial_amount.is_finite() or initial_amount < 0:
                        raise ValueError("initial stock must be a finite non-negative number")
                    self._validate_stock_number(initial_amount, "initial stock")
            now = utc_now()
            record = {
                "record_id": record_id or str(uuid.uuid4()), "form_id": form_id, "values": clean_values,
                "created_at": existing.get("created_at", now) if existing else now,
                "created_by": existing.get("created_by", actor) if existing else actor,
                "updated_at": now, "updated_by": actor,
            }
            if line_items is not None:
                record["line_items"] = line_items
            if existing and form_id == "product" and "stock_adjustments" in existing:
                record["stock_adjustments"] = copy.deepcopy(existing["stock_adjustments"])
            payload["records"] = [item for item in payload["records"] if item.get("record_id") != record["record_id"]] + [record]
            atomic_json_write(self.records_path, payload)
            self.history.record("form_record_updated" if existing else "form_record_created", actor, "forms", record["record_id"], self._history_snapshot(record))
        return record

    def adjust_product_stock(
        self, record_id: str, magnitude: Any, direction: str, reason: str,
        actor: str, expected_updated_at: str, request_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """Apply one idempotent, auditable stock movement to a product."""
        self._require_actor(actor)
        direction = str(direction).strip().lower()
        if direction not in {"add", "remove", "set"}:
            raise ValueError("choose a stock movement or a counted-stock correction")
        reason = " ".join(str(reason or "").split())
        if not reason or len(reason) > 500:
            raise ValueError("enter a reason of at most 500 characters")
        request_id = str(request_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", request_id):
            raise ValueError("stock adjustment request is invalid; reload and try again")
        if len(str(magnitude or "")) > 64:
            raise ValueError("adjustment amount is too large")
        amount = self._amount(magnitude, "adjustment amount")
        if not amount.is_finite() or (direction == "set" and amount < 0) or (direction != "set" and amount <= 0):
            raise ValueError("enter a finite non-negative count, or a positive adjustment amount")
        self._validate_stock_number(amount, "adjustment amount")
        product = self.definition("product")
        if not any(field.get("key") == "stock" and field.get("type") == "number" for field in product.get("fields", [])):
            raise ValueError("product stock adjustment is unavailable for this form definition")
        with exclusive_file_lock(self.control / ".forms-write.lock"):
            payload = self._read(self.records_path, {"records": []})
            record = next((item for item in payload["records"] if item.get("record_id") == record_id and item.get("form_id") == "product"), None)
            if record is None:
                raise ValueError("unknown product record")
            adjustments = record.get("stock_adjustments", [])
            if not isinstance(adjustments, list):
                adjustments = []
            old_operation = next((item for item in adjustments if item.get("request_id") == request_id), None)
            fingerprint = (direction, self._format_stock_amount(amount), reason)
            if old_operation:
                prior = (old_operation.get("direction"), old_operation.get("amount"), old_operation.get("reason"))
                if prior != fingerprint or old_operation.get("actor") != actor:
                    raise ValueError("this stock adjustment request was already used with different details")
                return copy.deepcopy(record), True
            if not expected_updated_at or expected_updated_at != record.get("updated_at", ""):
                raise ValueError("product changed since it was opened; reload before adjusting stock")
            current_text = record.get("values", {}).get("stock", "")
            current_was_invalid = False
            try:
                current = self._amount(current_text, "current stock") if str(current_text).strip() else Decimal("0")
            except ValueError:
                if direction != "set":
                    raise ValueError("current stock is invalid; use a counted-stock correction to recover it")
                current = Decimal("0")
                current_was_invalid = True
            try:
                self._validate_stock_number(current, "current stock")
                if current < 0:
                    raise ValueError("current stock is invalid")
            except ValueError:
                if direction != "set":
                    raise ValueError("current stock is invalid; use a counted-stock correction to recover it")
                current_was_invalid = True
            result = amount if direction == "set" else current + amount if direction == "add" else current - amount
            if result < 0:
                raise ValueError("stock cannot be reduced below zero")
            self._validate_stock_number(result, "resulting stock")
            before_text = str(current_text).strip() if current_was_invalid else self._format_stock_amount(current)
            after_text = self._format_stock_amount(result)
            now = utc_now()
            operation = {
                "adjustment_id": str(uuid.uuid4()), "request_id": request_id,
                "occurred_at": now, "actor": actor, "direction": direction,
                "amount": self._format_stock_amount(amount), "before": before_text,
                "after": after_text, "reason": reason,
            }
            updated = copy.deepcopy(record)
            updated.setdefault("values", {})["stock"] = after_text
            updated["stock_adjustments"] = [*adjustments, operation]
            updated["updated_at"] = now
            updated["updated_by"] = actor
            payload["records"] = [item for item in payload["records"] if item.get("record_id") != record_id] + [updated]
            atomic_json_write(self.records_path, payload)
            # The embedded movement survives a crash or retry even if the secondary
            # tamper-evident snapshot write below fails after this atomic commit.
            try:
                self.history.record("product_stock_adjusted", actor, "forms", record_id, self._history_snapshot(updated))
            except Exception as exc:
                raise StockAdjustmentCommittedError(
                    "stock movement is saved in the product record, but the secondary revision history could not be updated"
                ) from exc
        return updated, False

    @staticmethod
    def _format_stock_amount(value: Decimal) -> str:
        rendered = format(value.normalize(), "f")
        return rendered if "." not in rendered else rendered.rstrip("0").rstrip(".")

    @staticmethod
    def _validate_stock_number(value: Decimal, label: str) -> None:
        parts = value.as_tuple()
        if (not value.is_finite() or len(parts.digits) > 18 or
                parts.exponent < -12 or parts.exponent > 12 or
                (value != 0 and value.adjusted() > 12)):
            raise ValueError(f"{label} is outside the supported precision")

    @staticmethod
    def _history_snapshot(record: dict[str, Any]) -> dict[str, Any]:
        """Keep product snapshots useful without copying the full movement ledger."""
        snapshot = copy.deepcopy(record)
        movements = snapshot.pop("stock_adjustments", None)
        if isinstance(movements, list) and movements:
            snapshot["stock_adjustment_count"] = len(movements)
            snapshot["last_stock_adjustment"] = movements[-1]
        return snapshot

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
        form = {"form_id": form_id, "name": str(raw.get("name", form_id)).strip() or form_id, "description": str(raw.get("description", "")).strip(), "title_field": title_field, "fields": fields}
        if raw.get("layout") == "invoice":
            form["layout"] = "invoice"
        return form

    @staticmethod
    def _upgrade_standard_definitions(forms: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Move contacts out of the form catalogue and upgrade the stock invoice.

        Custom forms keep their definition.  The formerly generated contact
        records remain in the JSON store for recovery, but are no longer shown
        as a second contact source.
        """
        result = [copy.deepcopy(item) for item in forms if item.get("form_id") != "contact"]
        invoice = next((item for item in result if item.get("form_id") == "invoice"), None)
        if invoice and invoice.get("name") == "Rechnung" and invoice.get("layout") != "invoice":
            replacement = next(item for item in DEFAULT_FORMS if item["form_id"] == "invoice")
            result = [copy.deepcopy(replacement) if item.get("form_id") == "invoice" else item for item in result]
        return result

    @staticmethod
    def _amount(value: Any, label: str) -> Decimal:
        text = str(value or "").strip().replace(" ", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"invalid number: {label}") from exc
        return amount

    def _normalize_line_items(self, raw: Any) -> list[dict[str, str]]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "[]")
            except json.JSONDecodeError as exc:
                raise ValueError("invoice positions are invalid") from exc
        if not isinstance(raw, list):
            raise ValueError("invoice positions are invalid")
        items: list[dict[str, str]] = []
        for index, source in enumerate(raw, start=1):
            if not isinstance(source, dict):
                raise ValueError("invoice position is invalid")
            description = str(source.get("description", "")).strip()
            if not description and not any(str(source.get(key, "")).strip() for key in ("quantity", "unit_price", "product_id")):
                continue
            if not description:
                raise ValueError(f"invoice position {index}: description is required")
            quantity = self._amount(source.get("quantity"), f"quantity in position {index}")
            unit_price = self._amount(source.get("unit_price"), f"unit price in position {index}")
            tax_rate = self._amount(source.get("tax_rate", "0"), f"tax rate in position {index}")
            if quantity <= 0 or unit_price < 0 or tax_rate < 0:
                raise ValueError(f"invoice position {index}: quantity, price and tax must not be negative")
            items.append({
                "product_id": str(source.get("product_id", "")).strip(), "description": description,
                "quantity": self._format_amount(quantity), "unit": str(source.get("unit", "Stk.")).strip() or "Stk.",
                "unit_price": self._format_amount(unit_price), "tax_rate": self._format_amount(tax_rate),
            })
        if not items:
            raise ValueError("an invoice needs at least one position")
        return items

    def _invoice_totals(self, items: list[dict[str, str]]) -> dict[str, str]:
        net = sum((self._amount(item["quantity"], "quantity") * self._amount(item["unit_price"], "unit price") for item in items), Decimal("0"))
        tax = sum((self._amount(item["quantity"], "quantity") * self._amount(item["unit_price"], "unit price") * self._amount(item["tax_rate"], "tax rate") / Decimal("100") for item in items), Decimal("0"))
        return {"net_amount": self._format_amount(net), "tax_amount": self._format_amount(tax), "gross_amount": self._format_amount(net + tax)}

    @staticmethod
    def _format_amount(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")

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
