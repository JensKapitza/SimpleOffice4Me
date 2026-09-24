"""Audited shopping lists with explicit sharing and per-list permissions."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory

STATUSES = {"open", "taken", "bought", "not_found", "unavailable", "deferred"}
PERMISSIONS = {"read", "add", "edit", "complete", "manage"}
PRINCIPAL_TYPES = {"user", "contact"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")



def _gtin_check_digit(body: str) -> int:
    total = 0
    for index, digit in enumerate(reversed(body)):
        total += int(digit) * (3 if index % 2 == 0 else 1)
    return (10 - (total % 10)) % 10


def _valid_gtin(value: str) -> bool:
    return value.isdigit() and len(value) in {8, 12, 13, 14} and _gtin_check_digit(value[:-1]) == int(value[-1])


def _expand_upce(value: str) -> str:
    if len(value) != 8 or not value.isdigit() or value[0] not in {"0", "1"}:
        return ""
    ns, payload, check = value[0], value[1:7], value[7]
    a, b, c3, d, e, f6 = payload
    if f6 in "012":
        body = ns + a + b + f6 + "0000" + c3 + d + e
    elif f6 == "3":
        body = ns + a + b + c3 + "00000" + d + e
    elif f6 == "4":
        body = ns + a + b + c3 + d + "00000" + e
    else:
        body = ns + a + b + c3 + d + e + "0000" + f6
    return body + check


def normalize_barcode(value: Any) -> str:
    """Return canonical supported EAN/UPC/GTIN digits or raise ValueError."""
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    if len(digits) == 8:
        if _valid_gtin(digits):
            return digits
        expanded = _expand_upce(digits)
        if expanded and _valid_gtin(expanded):
            return digits
        raise ValueError("invalid EAN-8/UPC-E barcode")
    if not _valid_gtin(digits):
        raise ValueError("invalid EAN/UPC/GTIN barcode")
    return digits


class ShoppingStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "shopping.json"
        self.lock = self.root / CONTROL_DIR / ".shopping-write.lock"
        self.history = RevisionHistory(self.root)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {
            "schema_version": 3,
            "lists": list(value.get("lists", [])) if isinstance(value.get("lists"), list) else [],
            "items": list(value.get("items", [])) if isinstance(value.get("items"), list) else [],
            "shares": list(value.get("shares", [])) if isinstance(value.get("shares"), list) else [],
            "products": list(value.get("products", [])) if isinstance(value.get("products"), list) else [],
        }

    def _write(self, data: dict[str, Any]) -> None:
        data["schema_version"] = 3
        atomic_json_write(self.path, data)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]

    @classmethod
    def _product_key(cls, values: dict[str, Any]) -> str:
        barcode = str(values.get("barcode", "") or "").strip()
        if barcode:
            return f"barcode:{barcode}"
        fields = (
            cls._text(values.get("name", ""), 300).casefold(),
            cls._text(values.get("brand", ""), 160).casefold(),
            cls._text(values.get("pack_size", ""), 120).casefold(),
            cls._text(values.get("unit", ""), 40).casefold(),
        )
        return "text:" + "|".join(fields)

    def _remember_product(
        self,
        data: dict[str, Any],
        actor: str,
        item: dict[str, Any],
        *,
        bought: bool = False,
    ) -> dict[str, Any] | None:
        name = self._text(item.get("name", ""), 300)
        owner = self._text(actor, 200)
        if not name or not owner:
            return None
        product_key = self._product_key(item)
        row = next(
            (
                product for product in data["products"]
                if product.get("owner") == owner and product.get("product_key") == product_key
            ),
            None,
        )
        now = utc_now()
        if row is None:
            row = {
                "product_id": str(uuid.uuid4()),
                "item_id": str(item.get("item_id", "") or ""),
                "owner": owner,
                "product_key": product_key,
                "barcode": str(item.get("barcode", "") or ""),
                "name": name,
                "brand": "",
                "pack_size": "",
                "quantity": "",
                "unit": "",
                "category": "",
                "known_stores": [],
                "last_store": "",
                "last_price": "",
                "favorite": False,
                "purchase_count": 0,
                "last_bought_at": "",
                "created_at": now,
                "updated_at": now,
            }
            data["products"].append(row)
        for key, limit in (
            ("name", 300),
            ("brand", 160),
            ("pack_size", 120),
            ("quantity", 80),
            ("unit", 40),
            ("category", 120),
        ):
            value = self._text(item.get(key, ""), limit)
            if value:
                row[key] = value
        item_id = str(item.get("item_id", "") or "").strip()
        if item_id:
            row["item_id"] = item_id
        barcode = str(item.get("barcode", "") or "").strip()
        if barcode:
            row["barcode"] = barcode
        store = self._text(item.get("store", ""), 240)
        if not store:
            list_id = str(item.get("list_id", "") or "")
            list_row = next(
                (value for value in data["lists"] if value.get("list_id") == list_id),
                None,
            )
            if list_row is not None:
                store = self._text(list_row.get("default_store", ""), 240)
        if store:
            row["last_store"] = store
            known = [
                self._text(value, 240)
                for value in row.get("known_stores", [])
                if self._text(value, 240)
            ]
            if store.casefold() not in {value.casefold() for value in known}:
                known.append(store)
            row["known_stores"] = known[-20:]
        price = self._text(item.get("price", ""), 80)
        if price:
            row["last_price"] = price
        if bought:
            row["purchase_count"] = int(row.get("purchase_count", 0) or 0) + 1
            row["last_bought_at"] = now
        row["updated_at"] = now
        return row

    @staticmethod
    def _permissions(values: Any) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            raise ValueError("shopping share permissions must be a list")
        permissions = {str(value).strip() for value in values}
        if not permissions or not permissions <= PERMISSIONS:
            raise ValueError("invalid shopping share permissions")
        permissions.add("read")
        return sorted(permissions)

    @staticmethod
    def _share_permissions(data: dict[str, Any], list_id: str, actor: str) -> set[str]:
        row = next((item for item in data["lists"] if item.get("list_id") == list_id), None)
        if row is None:
            return set()
        if row.get("owner") == actor:
            return set(PERMISSIONS)
        result: set[str] = set()
        for share in data["shares"]:
            if share.get("list_id") == list_id and share.get("principal") == actor:
                result.update(str(value) for value in share.get("permissions", []) if value in PERMISSIONS)
        return result

    def _require(self, data: dict[str, Any], list_id: str, actor: str, permission: str) -> dict[str, Any]:
        row = next((item for item in data["lists"] if item.get("list_id") == list_id), None)
        if row is None or permission not in self._share_permissions(data, list_id, actor):
            raise ValueError("shopping list not found")
        return row

    def permissions(self, actor: str, list_id: str) -> set[str]:
        """Return effective permissions without exposing private list metadata."""
        data = self._read()
        return set(self._share_permissions(data, str(list_id), str(actor)))

    def lists(self, actor: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        data = self._read()
        visible_ids = {
            row["list_id"] for row in data["lists"]
            if "read" in self._share_permissions(data, str(row.get("list_id", "")), actor)
        }
        rows = [dict(row) for row in data["lists"] if row.get("list_id") in visible_ids]
        if not include_archived:
            rows = [row for row in rows if not row.get("archived")]
        return sorted(rows, key=lambda row: (str(row.get("name", "")).casefold(), str(row.get("created_at", ""))))

    def create_list(self, name: str, actor: str, *, list_id: str = "", store: str = "") -> dict[str, Any]:
        name = self._text(name, 200)
        actor = self._text(actor, 200)
        if not name or not actor:
            raise ValueError("shopping list name and actor are required")
        list_id = list_id.strip() or str(uuid.uuid4())
        if not _SAFE_ID.fullmatch(list_id):
            raise ValueError("invalid shopping list identifier")
        now = utc_now()
        row = {
            "list_id": list_id, "name": name, "owner": actor, "default_store": self._text(store, 240),
            "archived": False, "created_at": now, "updated_at": now, "updated_by": actor,
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            if any(item.get("list_id") == list_id for item in data["lists"]):
                raise ValueError("shopping list already exists")
            data["lists"].append(row)
            self._write(data)
        self.history.record("shopping_list_created", actor, "shopping-list", list_id, row)
        return dict(row)

    def share_list(
        self,
        list_id: str,
        actor: str,
        principal: str,
        permissions: list[str],
        *,
        principal_type: str = "user",
    ) -> dict[str, Any]:
        principal = self._text(principal, 200)
        principal_type = str(principal_type).strip()
        if not principal or principal_type not in PRINCIPAL_TYPES:
            raise ValueError("invalid shopping share principal")
        granted = self._permissions(permissions)
        with exclusive_file_lock(self.lock):
            data = self._read()
            self._require(data, list_id, actor, "manage")
            row = next((
                share for share in data["shares"]
                if share.get("list_id") == list_id
                and share.get("principal") == principal
                and share.get("principal_type") == principal_type
            ), None)
            now = utc_now()
            if row is None:
                row = {
                    "share_id": str(uuid.uuid4()), "list_id": list_id,
                    "principal": principal, "principal_type": principal_type,
                    "permissions": granted, "created_at": now, "created_by": actor,
                }
                data["shares"].append(row)
            else:
                row["permissions"] = granted
            row.update({"updated_at": now, "updated_by": actor})
            self._write(data)
        self.history.record("shopping_list_shared", actor, "shopping-list", list_id, {
            "principal": principal, "principal_type": principal_type, "permissions": granted,
        })
        return dict(row)

    def revoke_share(self, list_id: str, actor: str, principal: str, *, principal_type: str = "user") -> None:
        with exclusive_file_lock(self.lock):
            data = self._read()
            self._require(data, list_id, actor, "manage")
            before = len(data["shares"])
            data["shares"] = [
                share for share in data["shares"]
                if not (
                    share.get("list_id") == list_id
                    and share.get("principal") == principal
                    and share.get("principal_type") == principal_type
                )
            ]
            if len(data["shares"]) == before:
                raise ValueError("shopping share not found")
            self._write(data)
        self.history.record("shopping_list_share_revoked", actor, "shopping-list", list_id, {
            "principal": self._text(principal, 200), "principal_type": principal_type,
        })

    def archive_list(self, list_id: str, actor: str, archived: bool = True) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = self._require(data, list_id, actor, "manage")
            row.update({"archived": bool(archived), "updated_at": utc_now(), "updated_by": actor})
            self._write(data)
        self.history.record("shopping_list_updated", actor, "shopping-list", list_id, row)
        return dict(row)

    def items(self, actor: str, *, list_id: str = "", include_bought: bool = True) -> list[dict[str, Any]]:
        data = self._read()
        visible = {
            row["list_id"] for row in data["lists"]
            if "read" in self._share_permissions(data, str(row.get("list_id", "")), actor)
        }
        rows = [dict(row) for row in data["items"] if row.get("list_id") in visible]
        if list_id:
            if list_id not in visible:
                raise ValueError("shopping list not found")
            rows = [row for row in rows if row.get("list_id") == list_id]
        if not include_bought:
            rows = [row for row in rows if row.get("status") != "bought"]
        return sorted(rows, key=lambda row: (row.get("status") == "bought", -int(row.get("priority", 0)), row.get("created_at", "")))

    def items_by_store(
        self,
        actor: str,
        *,
        store: str = "",
        only_open: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """Group visible items from active lists by their effective store.

        An item-specific store overrides the list default. Empty-store items are
        grouped under an empty key so callers can still show unassigned needs.
        No location lookup or tracking is performed here.
        """
        data = self._read()
        visible_lists = {
            str(row.get("list_id", "")): row
            for row in data["lists"]
            if not row.get("archived")
            and "read" in self._share_permissions(data, str(row.get("list_id", "")), actor)
        }
        wanted = self._text(store, 240).casefold()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for source in data["items"]:
            list_id = str(source.get("list_id", ""))
            list_row = visible_lists.get(list_id)
            if list_row is None:
                continue
            if only_open and source.get("status") == "bought":
                continue
            effective = self._text(source.get("store") or list_row.get("default_store", ""), 240)
            if wanted and effective.casefold() != wanted:
                continue
            row = dict(source)
            row["effective_store"] = effective
            row["list_name"] = str(list_row.get("name", ""))
            grouped.setdefault(effective, []).append(row)
        for rows in grouped.values():
            rows.sort(key=lambda row: (-int(row.get("priority", 0)), str(row.get("created_at", ""))))
        return dict(sorted(grouped.items(), key=lambda item: item[0].casefold()))

    def products(self, actor: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return private local product memory for one actor."""
        owner = self._text(actor, 200)
        rows = [
            dict(row) for row in self._read()["products"]
            if row.get("owner") == owner
        ]
        rows.sort(
            key=lambda row: (
                bool(row.get("favorite")),
                int(row.get("purchase_count", 0) or 0),
                str(row.get("last_bought_at", "")),
                str(row.get("updated_at", "")),
                str(row.get("name", "")).casefold(),
            ),
            reverse=True,
        )
        return rows[:max(0, min(int(limit), 200))]

    def set_product_favorite(self, product_id: str, actor: str, favorite: bool) -> dict[str, Any]:
        owner = self._text(actor, 200)
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next(
                (
                    product for product in data["products"]
                    if product.get("product_id") == product_id and product.get("owner") == owner
                ),
                None,
            )
            if row is None:
                raise ValueError("shopping product not found")
            row["favorite"] = bool(favorite)
            row["updated_at"] = utc_now()
            self._write(data)
        self.history.record(
            "shopping_product_favorite_updated",
            owner,
            "shopping-product",
            product_id,
            {"favorite": bool(favorite)},
        )
        return dict(row)

    def find_known_barcode(self, actor: str, barcode: str) -> dict[str, Any] | None:
        """Return private product memory first, then newest visible list knowledge."""
        code = normalize_barcode(barcode)
        if not code:
            return None
        owner = self._text(actor, 200)
        data = self._read()
        product = next(
            (
                row for row in data["products"]
                if row.get("owner") == owner and str(row.get("barcode", "")) == code
            ),
            None,
        )
        if product is not None:
            result = dict(product)
            result["store"] = str(result.get("last_store", ""))
            result["price"] = str(result.get("last_price", ""))
            return result
        rows = [
            row for row in self.items(actor, include_bought=True)
            if str(row.get("barcode", "")) == code
        ]
        if not rows:
            return None
        rows.sort(key=lambda row: str(row.get("updated_at", "")), reverse=True)
        return dict(rows[0])

    def add_item(self, list_id: str, name: str, actor: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        values = values or {}
        name = self._text(name, 300)
        if not name:
            raise ValueError("shopping item name is required")
        status = str(values.get("status", "open")).strip()
        if status not in STATUSES:
            raise ValueError("invalid shopping item status")
        try:
            priority = max(0, min(9, int(values.get("priority", 0) or 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid shopping item priority") from exc
        now = utc_now()
        request_id = self._text(values.get("request_id", ""), 80)
        if request_id and not _SAFE_ID.fullmatch(request_id):
            raise ValueError("invalid shopping request identifier")
        item = {
            "item_id": str(uuid.uuid4()), "list_id": list_id, "name": name,
            "quantity": self._text(values.get("quantity", ""), 80),
            "unit": self._text(values.get("unit", ""), 40),
            "note": self._text(values.get("note", ""), 2000),
            "category": self._text(values.get("category", ""), 120),
            "store": self._text(values.get("store", ""), 240),
            "brand": self._text(values.get("brand", ""), 160),
            "pack_size": self._text(values.get("pack_size", ""), 120),
            "price": self._text(values.get("price", ""), 80),
            "barcode": normalize_barcode(values.get("barcode", "")),
            "priority": priority, "status": status,
            "assigned_to": self._text(values.get("assigned_to", ""), 200),
            "request_id": request_id,
            "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
            "completed_at": now if status == "bought" else "",
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            self._require(data, list_id, actor, "add")
            if request_id:
                existing = next(
                    (
                        row for row in data["items"]
                        if row.get("list_id") == list_id
                        and row.get("created_by") == actor
                        and row.get("request_id") == request_id
                    ),
                    None,
                )
                if existing is not None:
                    return dict(existing)
            data["items"].append(item)
            self._remember_product(data, actor, item, bought=status == "bought")
            self._write(data)
        self.history.record("shopping_item_created", actor, "shopping-item", item["item_id"], item)
        return dict(item)

    def update_item(self, item_id: str, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            item = next((row for row in data["items"] if row.get("item_id") == item_id), None)
            if item is None:
                raise ValueError("shopping item not found")
            list_id = str(item.get("list_id", ""))
            permissions = self._share_permissions(data, list_id, actor)
            if not permissions:
                raise ValueError("shopping item not found")
            content_keys = {
                "name", "quantity", "unit", "note", "category", "store", "barcode",
                "priority", "brand", "pack_size", "price",
            }
            completion_keys = {"status", "assigned_to"}
            if content_keys.intersection(values) and "edit" not in permissions:
                raise ValueError("shopping item not found")
            if completion_keys.intersection(values) and "complete" not in permissions:
                raise ValueError("shopping item not found")
            before = dict(item)
            if "status" in values:
                status = str(values["status"]).strip()
                if status not in STATUSES:
                    raise ValueError("invalid shopping item status")
                item["status"] = status
                item["completed_at"] = utc_now() if status == "bought" else ""
            for key, limit in (
                ("name", 300), ("quantity", 80), ("unit", 40), ("note", 2000),
                ("category", 120), ("store", 240), ("assigned_to", 200),
                ("brand", 160), ("pack_size", 120), ("price", 80),
            ):
                if key in values:
                    item[key] = self._text(values[key], limit)
            if "barcode" in values:
                item["barcode"] = normalize_barcode(values["barcode"])
            if not item.get("name"):
                raise ValueError("shopping item name is required")
            if "priority" in values:
                try:
                    item["priority"] = max(0, min(9, int(values["priority"])))
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid shopping item priority") from exc
            item.update({"updated_at": utc_now(), "updated_by": actor})
            self._remember_product(
                data,
                actor,
                item,
                bought=before.get("status") != "bought" and item.get("status") == "bought",
            )
            self._write(data)
        self.history.record("shopping_item_updated", actor, "shopping-item", item_id, {"before": before, "after": item})
        return dict(item)

    def take_item(self, item_id: str, actor: str) -> dict[str, Any]:
        return self.update_item(item_id, actor, {"status": "taken", "assigned_to": actor})
