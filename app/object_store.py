"""File-based register for physical, virtual and billable catalog objects."""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory

OBJECT_STATES = {"active", "inactive", "lost", "retired"}
STOCK_ADJUST_REASONS = {"zaehlfehler", "schwund", "zugang", "korrektur"}
OBJECT_CONDITIONS = {"new": "Neu", "used": "Gebraucht", "defective": "Defekt",
                     "in_repair": "In Reparatur", "retired": "Ausgesondert"}
RELATION_KINDS = {"accessory": "Zubehör", "member": "Set-Bestandteil"}
MONEY = Decimal("0.01")
INVOICE_DEFAULTS: dict[str, Any] = {
    "use_in_invoice": False,
    "is_category": False,
    "category_object_id": "",
    "category": "",
    "description": "",
    "net_price": "",
    "gross_price": "",
    "vat_rate": "",
    "price_group": "",
    "default_net_price": "",
    "default_gross_price": "",
    "default_vat_rate": "",
    "default_price_group": "",
}


class ObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "objects"
        self.history = RevisionHistory(self.root)
        self.sequence_path = self.directory / "sequence.json"
        self.sequence_lock = self.directory / ".sequence-write.lock"

    def objects(self, query: str = "") -> list[dict[str, Any]]:
        self._ensure_sequences()
        self.directory.mkdir(parents=True, exist_ok=True)
        needle = query.strip().casefold()
        numeric_needle = self._sequence_reference(query)
        objects = [
            self._normalized_item(item)
            for path in self.directory.glob("*.json")
            if path.name != "sequence.json" and (item := self._read(path))
        ]
        width = self.sequence_width(objects)
        for item in objects:
            item["display_id"] = self.format_sequence(item.get("sequence_id", 0), width)
            item["original_display_id"] = str(int(item.get("sequence_id", 0) or 0))
            item["invoice_effective"] = self.invoice_effective(item)
        if needle:
            objects = [
                item for item in objects
                if (numeric_needle is not None and int(item.get("sequence_id", 0) or 0) == numeric_needle)
                or needle in json.dumps(item, ensure_ascii=False).casefold()
            ]
        return sorted(objects, key=lambda item: (int(item.get("sequence_id", 0)), item.get("name", "").casefold()))

    def iter_objects(self):
        """Iterate catalog entries without loading the entire inventory."""
        self._ensure_sequences()
        width = self.sequence_width()
        for path in self.directory.glob("*.json"):
            if path.name == "sequence.json":
                continue
            raw = self._read(path)
            if not raw:
                continue
            item = self._normalized_item(raw)
            item["display_id"] = self.format_sequence(item.get("sequence_id", 0), width)
            yield item

    def low_stock(self) -> list[dict[str, Any]]:
        """Bulk items whose quantity fell below their minimum stock."""
        rows = []
        for item in self.objects():
            quantity, minimum = str(item.get("quantity", "") or ""), str(item.get("min_stock", "") or "")
            if not quantity or not minimum:
                continue
            try:
                low = Decimal(quantity) < Decimal(minimum)
            except InvalidOperation:
                continue
            if low:
                rows.append(item)
        return sorted(rows, key=lambda item: (item.get("name", "").casefold(), int(item.get("sequence_id", 0))))

    def object(self, object_id: str) -> dict[str, Any]:
        """Resolve a UUID or any zero-padded representation of the permanent sequence ID."""
        self._ensure_sequences()
        reference = str(object_id or "").strip()
        if re.fullmatch(r"[0-9a-f-]{36}", reference):
            item = self._read(self.directory / f"{reference}.json")
        else:
            sequence = self._sequence_reference(reference)
            item = self._find_by_sequence(sequence) if sequence is not None else None
        if not item:
            raise ValueError("unknown object")
        item = self._normalized_item(item)
        sequence_id = int(item.get("sequence_id", 0) or 0)
        item["display_id"] = self.format_sequence(sequence_id, self.sequence_width())
        item["original_display_id"] = str(sequence_id)
        item["invoice_effective"] = self.invoice_effective(item)
        return item

    def create(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        self._require(actor, values.get("name", ""), values.get("type", ""))
        expires_at = self._date(values.get("expires_at", ""))
        now = utc_now()
        item = {
            "object_id": str(uuid.uuid4()), "sequence_id": self._next_sequence(),
            "name": str(values["name"]).strip(), "type": str(values["type"]).strip(),
            "status": self._status(values.get("status", "active")),
            "condition": self._condition(values.get("condition", "")),
            "description": str(values.get("description", "")).strip(),
            "identifier": str(values.get("identifier", "")).strip(),
            "location": str(values.get("location", "")).strip(), "expires_at": expires_at,
            "quantity": self._quantity(values.get("quantity", "")),
            "unit": self._unit(values.get("unit", "")),
            "share_inventory": self._share_flag(values.get("share_inventory", True)),
            "min_stock": self._quantity(values.get("min_stock", "")),
            "tags": self._list(values.get("tags", "")), "fields": self._fields(values.get("fields", "")),
            "invoice": self._invoice_values(values), "document_ids": [], "notes": [],
            "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
        }
        self._write(item, actor, "object_created")
        return self.object(item["object_id"])

    def update(self, object_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        item = self.object(object_id)
        self._require(actor, values.get("name", ""), values.get("type", ""))
        item.update({
            "name": str(values["name"]).strip(), "type": str(values["type"]).strip(),
            "status": self._status(values.get("status", "active")),
            "condition": self._condition(values.get("condition", "")),
            "description": str(values.get("description", "")).strip(),
            "identifier": str(values.get("identifier", "")).strip(),
            "location": str(values.get("location", "")).strip(),
            "expires_at": self._date(values.get("expires_at", "")),
            "quantity": self._quantity(values.get("quantity", "")),
            "unit": self._unit(values.get("unit", "")),
            "share_inventory": self._share_flag(values.get("share_inventory", item.get("share_inventory", True))),
            "min_stock": self._quantity(values.get("min_stock", "")),
            "tags": self._list(values.get("tags", "")), "fields": self._fields(values.get("fields", "")),
            "invoice": self._invoice_values(values, existing=item.get("invoice", {})),
            "updated_at": utc_now(), "updated_by": actor,
        })
        for transient in ("display_id", "original_display_id", "invoice_effective"):
            item.pop(transient, None)
        self._write(item, actor, "object_updated")
        return self.object(item["object_id"])

    def invoice_candidates(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        needle = query.strip().casefold()
        numeric_needle = self._sequence_reference(query)
        rows: list[dict[str, Any]] = []
        for item in self.objects():
            invoice = self.invoice_effective(item)
            if not invoice.get("use_in_invoice") or item.get("status") != "active":
                continue
            haystack = " ".join((item.get("display_id", ""), item.get("original_display_id", ""), item.get("name", ""), item.get("identifier", ""), invoice.get("description", ""), invoice.get("category", ""))).casefold()
            if needle and not ((numeric_needle is not None and int(item.get("sequence_id", 0) or 0) == numeric_needle) or needle in haystack):
                continue
            rows.append({
                "object_id": item["object_id"], "sequence_id": item["sequence_id"],
                "id": item["display_id"], "original_id": item["original_display_id"], "name": item["name"],
                "description": invoice.get("description") or item.get("description") or item["name"],
                "category": invoice.get("category", ""), "category_object_id": invoice.get("category_object_id", ""),
                "net_price": invoice.get("net_price", "0.00"), "gross_price": invoice.get("gross_price", "0.00"),
                "vat_rate": invoice.get("vat_rate", "0"), "price_group": invoice.get("price_group", ""),
            })
            if len(rows) >= max(1, min(int(limit), 100)):
                break
        return rows

    def invoice_categories(self) -> list[dict[str, Any]]:
        return [item for item in self.objects() if bool(item.get("invoice", {}).get("is_category"))]

    def invoice_effective(self, item: dict[str, Any]) -> dict[str, Any]:
        current = self._normalized_invoice(item.get("invoice"))
        category_id = str(current.get("category_object_id", "")).strip()
        if category_id and category_id != item.get("object_id"):
            category = self._read(self.directory / f"{category_id}.json")
            defaults = self._normalized_invoice(category.get("invoice")) if category else dict(INVOICE_DEFAULTS)
            for key in ("vat_rate", "net_price", "gross_price", "price_group", "category"):
                fallback = defaults.get(f"default_{key}", defaults.get(key, ""))
                if not str(current.get(key, "")).strip() and str(fallback).strip():
                    current[key] = fallback
        return self._reconcile_prices(current)

    @classmethod
    def _normalized_invoice(cls, value: Any) -> dict[str, Any]:
        invoice = dict(INVOICE_DEFAULTS)
        if isinstance(value, dict):
            invoice.update({key: item for key, item in value.items() if key in invoice})
        invoice["use_in_invoice"] = cls._bool(invoice["use_in_invoice"])
        invoice["is_category"] = cls._bool(invoice["is_category"])
        for key in set(invoice) - {"use_in_invoice", "is_category"}:
            invoice[key] = str(invoice[key] or "").strip()
        return invoice

    @classmethod
    def _normalized_item(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Supply the current read model for historical records without rewriting them."""
        item = dict(value)
        for key in (
            "object_id", "name", "type", "description", "identifier", "location",
            "expires_at", "created_at", "created_by", "updated_at", "updated_by",
            "unit",
        ):
            item[key] = str(item.get(key, "") or "").strip()
        try:
            item["quantity"] = cls._quantity(item.get("quantity", ""))
        except ValueError:
            item["quantity"] = ""
        item["share_inventory"] = True if "share_inventory" not in item else bool(item.get("share_inventory"))
        try:
            item["min_stock"] = cls._quantity(item.get("min_stock", ""))
        except ValueError:
            item["min_stock"] = ""
        status = str(item.get("status", "") or "").strip()
        item["status"] = status if status in OBJECT_STATES else "active"
        condition = str(item.get("condition", "") or "").strip()
        item["condition"] = condition if condition in OBJECT_CONDITIONS else ""
        try:
            item["sequence_id"] = max(0, int(item.get("sequence_id", 0) or 0))
        except (TypeError, ValueError):
            item["sequence_id"] = 0
        item["tags"] = cls._list(item.get("tags", []))
        item["fields"] = (
            {str(key).strip(): str(field).strip() for key, field in item.get("fields", {}).items() if str(key).strip()}
            if isinstance(item.get("fields"), dict) else {}
        )
        item["document_ids"] = cls._list(item.get("document_ids", []))
        relations = item.get("relations", [])
        item["relations"] = [
            {
                "relation_id": str(entry.get("relation_id", "") or "").strip(),
                "kind": str(entry.get("kind", "") or "").strip(),
                "target_id": str(entry.get("target_id", "") or "").strip(),
                "note": str(entry.get("note", "") or "").strip(),
                "created_at": str(entry.get("created_at", "") or "").strip(),
                "created_by": str(entry.get("created_by", "") or "").strip(),
            }
            for entry in relations if isinstance(entry, dict)
            if str(entry.get("relation_id", "") or "").strip()
            and str(entry.get("kind", "") or "").strip() in RELATION_KINDS
            and str(entry.get("target_id", "") or "").strip()
        ] if isinstance(relations, list) else []
        overrides = item.get("overrides", {})
        item["overrides"] = {
            str(key).strip(): {
                "value": str(entry.get("value", "") or "").strip(),
                "updated_at": str(entry.get("updated_at", "") or "").strip(),
                "updated_by": str(entry.get("updated_by", "") or "").strip(),
            }
            for key, entry in overrides.items()
            if str(key).strip() and isinstance(entry, dict) and str(entry.get("value", "") or "").strip()
        } if isinstance(overrides, dict) else {}
        notes = item.get("notes", [])
        item["notes"] = [
            {
                "note_id": str(note.get("note_id", "") or "").strip(),
                "text": str(note.get("text", "") or "").strip(),
                "created_at": str(note.get("created_at", "") or "").strip(),
                "created_by": str(note.get("created_by", "") or "").strip(),
            }
            for note in notes if isinstance(note, dict)
        ] if isinstance(notes, list) else []
        item["invoice"] = cls._normalized_invoice(item.get("invoice"))
        raw_adjustments = item.get("stock_adjustments", [])
        item["stock_adjustments"] = [
            {
                "adjustment_id": str(row.get("adjustment_id", "") or "").strip(),
                "counted": str(row.get("counted", "") or "").strip(),
                "previous": str(row.get("previous", "") or "").strip(),
                "reason": str(row.get("reason", "") or "").strip(),
                "note": str(row.get("note", "") or "").strip(),
                "adjusted_at": str(row.get("adjusted_at", "") or "").strip(),
                "adjusted_by": str(row.get("adjusted_by", "") or "").strip(),
            }
            for row in raw_adjustments if isinstance(row, dict)
        ] if isinstance(raw_adjustments, list) else []
        return item

    def adjust_quantity(self, object_id: str, counted: Any, reason: str, note: str, actor: str) -> dict[str, Any]:
        """Reconcile counted stock with the recorded quantity and keep an audit trail."""
        item = self.object(object_id)
        if not str(actor).strip():
            raise ValueError("user is required")
        code = str(reason or "").strip().lower()
        if code not in STOCK_ADJUST_REASONS:
            raise ValueError("invalid stock adjustment reason")
        counted_quantity = self._quantity(counted)
        previous = str(item.get("quantity", "") or "")
        adjustments = item.get("stock_adjustments", [])
        if not isinstance(adjustments, list):
            adjustments = []
        adjustments.append({
            "adjustment_id": str(uuid.uuid4()), "counted": counted_quantity, "previous": previous,
            "reason": code, "note": str(note or "").strip()[:500],
            "adjusted_at": utc_now(), "adjusted_by": str(actor).strip(),
        })
        item["stock_adjustments"] = adjustments
        item["quantity"] = counted_quantity
        item["updated_at"] = utc_now()
        item["updated_by"] = str(actor).strip()
        self._drop_transient(item)
        self._write(item, str(actor).strip(), "object_quantity_adjusted")
        return self.object(item["object_id"])

    def attach_document(self, object_id: str, document_id: str, actor: str) -> None:
        item = self.object(object_id)
        if not actor.strip() or not document_id.strip(): raise ValueError("user and document are required")
        if document_id not in item["document_ids"]:
            item["document_ids"].append(document_id); item["updated_at"] = utc_now(); item["updated_by"] = actor
            self._drop_transient(item); self._write(item, actor, "object_document_attached")

    def detach_document(self, object_id: str, document_id: str, actor: str) -> None:
        item = self.object(object_id)
        if not actor.strip(): raise ValueError("user is required")
        if document_id not in item["document_ids"]: raise ValueError("document is not attached")
        item["document_ids"].remove(document_id); item["updated_at"] = utc_now(); item["updated_by"] = actor
        self._drop_transient(item); self._write(item, actor, "object_document_detached")

    def add_note(self, object_id: str, text: str, actor: str) -> None:
        item = self.object(object_id)
        if not actor.strip() or not text.strip(): raise ValueError("user and note are required")
        item["notes"].append({"note_id": str(uuid.uuid4()), "text": text.strip(), "created_at": utc_now(), "created_by": actor})
        item["updated_at"] = utc_now(); item["updated_by"] = actor
        self._drop_transient(item); self._write(item, actor, "object_note_added")

    def add_relation(self, object_id: str, kind: str, target_id: str, actor: str, note: str = "") -> dict[str, Any]:
        """Link an accessory or set member; identical links are idempotent."""
        item = self.object(object_id)
        clean_kind = str(kind or "").strip()
        if clean_kind not in RELATION_KINDS: raise ValueError("unknown relation kind")
        try:
            target = self.object(target_id)
        except ValueError as exc:
            raise ValueError("related object was not found") from exc
        if target["object_id"] == item["object_id"]:
            raise ValueError("an object cannot be related to itself")
        if not actor.strip(): raise ValueError("user is required")
        for entry in item["relations"]:
            if entry["kind"] == clean_kind and entry["target_id"] == target["object_id"]:
                return item
        if len(item["relations"]) >= 50:
            raise ValueError("too many relations for one object")
        item["relations"].append({
            "relation_id": str(uuid.uuid4()), "kind": clean_kind,
            "target_id": target["object_id"], "note": str(note or "").strip()[:500],
            "created_at": utc_now(), "created_by": actor,
        })
        item["updated_at"] = utc_now(); item["updated_by"] = actor
        self._drop_transient(item); self._write(item, actor, "object_relation_added")
        return self.object(item["object_id"])

    def remove_relation(self, object_id: str, relation_id: str, actor: str) -> dict[str, Any]:
        item = self.object(object_id)
        if not actor.strip(): raise ValueError("user is required")
        kept = [entry for entry in item["relations"] if entry["relation_id"] != str(relation_id or "").strip()]
        if len(kept) == len(item["relations"]): raise ValueError("relation was not found")
        item["relations"] = kept
        item["updated_at"] = utc_now(); item["updated_by"] = actor
        self._drop_transient(item); self._write(item, actor, "object_relation_removed")
        return self.object(item["object_id"])

    def related(self, object_id: str) -> dict[str, list[dict[str, Any]]]:
        """Outgoing relations plus incoming backlinks with resolved targets."""
        item = self.object(object_id)
        outgoing = []
        for entry in item["relations"]:
            try:
                target = self.object(entry["target_id"])
            except ValueError:
                target = None
            outgoing.append({"relation": entry, "target": target})
        incoming = []
        for candidate in self.objects():
            if candidate["object_id"] == item["object_id"]:
                continue
            for entry in candidate.get("relations", []):
                if entry.get("target_id") == item["object_id"]:
                    incoming.append({"relation": entry, "source": candidate})
        return {"outgoing": outgoing, "incoming": incoming}

    @staticmethod
    def _override_field(name: Any) -> str:
        field = str(name or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field):
            raise ValueError("metadata field name is invalid")
        return field

    def set_override(self, object_id: str, field: str, value: str, actor: str) -> dict[str, Any]:
        """Store a manual metadata value; the system value stays untouched."""
        item = self.object(object_id)
        key = self._override_field(field)
        text = str(value or "").strip()
        if not actor.strip() or not text:
            raise ValueError("user and manual value are required")
        if len(text) > 2000:
            raise ValueError("manual value must not exceed 2000 characters")
        item["overrides"][key] = {"value": text, "updated_at": utc_now(), "updated_by": actor}
        item["updated_at"] = utc_now(); item["updated_by"] = actor
        self._drop_transient(item); self._write(item, actor, "object_override_set")
        return self.object(item["object_id"])

    def clear_override(self, object_id: str, field: str, actor: str) -> dict[str, Any]:
        """Remove a manual value so the system value applies again."""
        item = self.object(object_id)
        key = self._override_field(field)
        if not actor.strip(): raise ValueError("user is required")
        if key not in item["overrides"]: raise ValueError("no manual value stored for this field")
        del item["overrides"][key]
        item["updated_at"] = utc_now(); item["updated_by"] = actor
        self._drop_transient(item); self._write(item, actor, "object_override_cleared")
        return self.object(item["object_id"])

    @staticmethod
    def effective_fields(item: dict[str, Any]) -> dict[str, str]:
        """Merge system fields with manual values; manual values win."""
        fields = dict(item.get("fields", {}) or {})
        overrides = item.get("overrides", {}) or {}
        for key, entry in overrides.items():
            if isinstance(entry, dict) and str(entry.get("value", "") or "").strip():
                fields[str(key)] = str(entry["value"]).strip()
        return fields

    @staticmethod
    def _drop_transient(item: dict[str, Any]) -> None:
        for key in ("display_id", "original_display_id", "invoice_effective"):
            item.pop(key, None)

    def sequence_width(self, items: list[dict[str, Any]] | None = None) -> int:
        if items is None:
            items = (item for path in self.directory.glob("*.json") if path.name != "sequence.json" and (item := self._read(path)))
        highest = max((int(item.get("sequence_id", 0) or 0) for item in items), default=0)
        highest = max(highest, int(self._read_sequence_state().get("last", 0) or 0))
        return max(1, len(str(highest or 1)))

    @staticmethod
    def format_sequence(value: Any, width: int) -> str:
        try: number = int(value)
        except (TypeError, ValueError): number = 0
        return str(max(0, number)).zfill(max(1, int(width)))

    @staticmethod
    def _sequence_reference(value: Any) -> int | None:
        """Return the numeric sequence represented by 8, 08, 008, #008, etc."""
        text = str(value or "").strip()
        if text.startswith("#"): text = text[1:].strip()
        if not text or not text.isdigit(): return None
        number = int(text, 10)
        return number if number > 0 else None

    def _find_by_sequence(self, sequence_id: int) -> dict[str, Any] | None:
        for path in self.directory.glob("*.json"):
            if path.name == "sequence.json": continue
            item = self._read(path)
            if item and int(item.get("sequence_id", 0) or 0) == sequence_id:
                return item
        return None

    def _next_sequence(self) -> int:
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.sequence_lock):
            state = self._read_sequence_state()
            last = max(int(state.get("last", 0) or 0), self._max_existing_sequence()) + 1
            atomic_json_write(self.sequence_path, {"last": last, "updated_at": utc_now()})
            return last

    def _ensure_sequences(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        missing = []
        for path in self.directory.glob("*.json"):
            if path.name == "sequence.json": continue
            item = self._read(path)
            if item and not int(item.get("sequence_id", 0) or 0):
                missing.append((path, str(item.get("created_at", "")), item.get("object_id", "")))
        if not missing: return
        # Keep only the legacy ordering keys, not every full metadata document.
        missing.sort(key=lambda row: (row[1], row[2]))
        with exclusive_file_lock(self.sequence_lock):
            state = self._read_sequence_state(); last = max(int(state.get("last", 0) or 0), self._max_existing_sequence())
            for path, _, _ in missing:
                fresh = self._read(path)
                if not fresh or int(fresh.get("sequence_id", 0) or 0): continue
                last += 1; fresh["sequence_id"] = last
                atomic_json_write(path, fresh)
                self.history.record("object_sequence_assigned", "system:migration", "objects", fresh["object_id"], {"sequence_id": last})
            atomic_json_write(self.sequence_path, {"last": last, "updated_at": utc_now()})

    def _max_existing_sequence(self) -> int:
        highest = 0
        for path in self.directory.glob("*.json"):
            if path.name == "sequence.json": continue
            item = self._read(path)
            if item:
                try: highest = max(highest, int(item.get("sequence_id", 0) or 0))
                except (TypeError, ValueError): pass
        return highest

    def _read_sequence_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.sequence_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"last": 0}
        except (OSError, json.JSONDecodeError): return {"last": 0}

    def _invoice_values(self, values: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
        previous = dict(existing or {})
        result = {
            "use_in_invoice": self._bool(values.get("use_in_invoice", previous.get("use_in_invoice", False))),
            "is_category": self._bool(values.get("is_invoice_category", values.get("is_category", previous.get("is_category", False)))),
            "category_object_id": str(values.get("category_object_id", previous.get("category_object_id", ""))).strip(),
            "category": str(values.get("invoice_category", previous.get("category", ""))).strip(),
            "description": str(values.get("invoice_description", previous.get("description", ""))).strip(),
            "net_price": self._decimal(values.get("net_price", previous.get("net_price", "")), allow_blank=True),
            "gross_price": self._decimal(values.get("gross_price", previous.get("gross_price", "")), allow_blank=True),
            "vat_rate": self._decimal(values.get("vat_rate", previous.get("vat_rate", "")), allow_blank=True, places="0.01"),
            "price_group": str(values.get("price_group", previous.get("price_group", ""))).strip(),
            "default_net_price": self._decimal(values.get("default_net_price", previous.get("default_net_price", "")), allow_blank=True),
            "default_gross_price": self._decimal(values.get("default_gross_price", previous.get("default_gross_price", "")), allow_blank=True),
            "default_vat_rate": self._decimal(values.get("default_vat_rate", previous.get("default_vat_rate", "")), allow_blank=True, places="0.01"),
            "default_price_group": str(values.get("default_price_group", previous.get("default_price_group", ""))).strip(),
        }
        if result["category_object_id"]:
            category = self._read(self.directory / f"{result['category_object_id']}.json")
            if not category or not bool(category.get("invoice", {}).get("is_category")):
                raise ValueError("selected invoice category does not exist")
        result = self._reconcile_prices(result)
        if result["use_in_invoice"] and not result.get("description"):
            result["description"] = str(values.get("description", "")).strip() or str(values.get("name", "")).strip()
        return result

    @classmethod
    def _reconcile_prices(cls, invoice: dict[str, Any]) -> dict[str, Any]:
        result = dict(invoice)
        try: vat = Decimal(str(result.get("vat_rate", "") or "0"))
        except InvalidOperation: vat = Decimal("0")
        net_text, gross_text = str(result.get("net_price", "")).strip(), str(result.get("gross_price", "")).strip()
        try: net = Decimal(net_text) if net_text else None
        except InvalidOperation: net = None
        try: gross = Decimal(gross_text) if gross_text else None
        except InvalidOperation: gross = None
        factor = Decimal("1") + vat / Decimal("100")
        if net is not None and gross is None: gross = (net * factor).quantize(MONEY, rounding=ROUND_HALF_UP)
        elif gross is not None and net is None and factor != 0: net = (gross / factor).quantize(MONEY, rounding=ROUND_HALF_UP)
        if net is not None: result["net_price"] = f"{net.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"
        if gross is not None: result["gross_price"] = f"{gross.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"
        return result

    @staticmethod
    def _bool(value: Any) -> bool:
        if isinstance(value, bool): return value
        return str(value or "").strip().casefold() in {"1", "true", "yes", "on", "checked"}

    @staticmethod
    def _decimal(value: Any, *, allow_blank: bool = False, places: str = "0.01") -> str:
        text = str(value or "").strip().replace(",", ".")
        if not text and allow_blank: return ""
        try: number = Decimal(text or "0")
        except InvalidOperation as exc: raise ValueError(f"invalid decimal value: {value}") from exc
        if number < 0: raise ValueError("prices and tax rates must not be negative")
        return format(number.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")

    @staticmethod
    def _require(actor: str, name: Any, object_type: Any) -> None:
        if not str(actor).strip() or not str(name).strip() or not str(object_type).strip(): raise ValueError("user, object name and type are required")

    @staticmethod
    def _status(value: Any) -> str:
        status = str(value).strip() or "active"
        if status not in OBJECT_STATES: raise ValueError("invalid object status")
        return status

    @staticmethod
    def _condition(value: Any) -> str:
        condition = str(value or "").strip()
        if condition and condition not in OBJECT_CONDITIONS:
            raise ValueError("invalid object condition")
        return condition

    @staticmethod
    def _date(value: Any) -> str:
        text = str(value or "").strip()
        if not text: return ""
        try: return date.fromisoformat(text).isoformat()
        except ValueError as exc: raise ValueError("object expiry must be an ISO date") from exc

    @staticmethod
    def _quantity(value: Any) -> str:
        """Normalize bulk quantities; blank means a single tracked piece."""
        text = str(value or "").strip().replace(",", ".")
        if not text: return ""
        try: number = Decimal(text)
        except InvalidOperation as exc: raise ValueError("object quantity must be a number") from exc
        if not number.is_finite(): raise ValueError("object quantity must be finite")
        if number < 0: raise ValueError("object quantity must not be negative")
        if number > 999999999: raise ValueError("object quantity is too large")
        quantized = number.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        return format(quantized.normalize(), "f")

    @staticmethod
    def _unit(value: Any) -> str:
        text = str(value or "").strip()
        if len(text) > 24: raise ValueError("object unit must not exceed 24 characters")
        return text

    @staticmethod
    def _share_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _list(value: Any) -> list[str]:
        values = value.split(",") if isinstance(value, str) else value or []
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    @staticmethod
    def _fields(value: Any) -> dict[str, str]:
        if isinstance(value, dict): return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip()}
        fields: dict[str, str] = {}
        for line in str(value or "").splitlines():
            if not line.strip(): continue
            if "=" not in line: raise ValueError("custom fields require one key=value pair per line")
            key, item = line.split("=", 1); key = key.strip()
            if not key: raise ValueError("custom field key is required")
            fields[key] = item.strip()
        return fields

    def _write(self, item: dict[str, Any], actor: str, action: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.directory / ".objects-write.lock"):
            atomic_json_write(self.directory / f"{item['object_id']}.json", item)
            self.history.record(action, actor, "objects", item["object_id"], item)

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("object_id") else None
        except (OSError, json.JSONDecodeError): return None
